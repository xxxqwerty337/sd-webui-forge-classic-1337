from __future__ import annotations

import datetime
import functools
import hashlib
import io
import json
import math
import os
import re
import string
import subprocess
import time
from collections import namedtuple

import numpy as np
import piexif
import piexif.helper
import pillow_jxl  # noqa
import pytz
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, PngImagePlugin
from pillow_heif import register_heif_opener

from modules import errors, script_callbacks, sd_samplers, shared
from modules.paths_internal import roboto_ttf_file
from modules.shared import opts

register_heif_opener()
LANCZOS = Image.Resampling.LANCZOS
NEAREST = Image.Resampling.NEAREST


def get_font(fontsize: int):
    return ImageFont.truetype(roboto_ttf_file, fontsize)


def image_grid(imgs, batch_size=1, rows=None):
    if rows is None:
        if opts.n_rows > 0:
            rows = opts.n_rows
        elif opts.n_rows == 0:
            rows = batch_size
        elif opts.grid_prevent_empty_spots:
            rows = math.floor(math.sqrt(len(imgs)))
            while len(imgs) % rows != 0:
                rows -= 1
        else:
            rows = math.sqrt(len(imgs))
            rows = round(rows)
    if rows > len(imgs):
        rows = len(imgs)

    cols = math.ceil(len(imgs) / rows)

    params = script_callbacks.ImageGridLoopParams(imgs, cols, rows)
    script_callbacks.image_grid_callback(params)

    w, h = map(max, zip(*(img.size for img in imgs)))
    grid_background_color = ImageColor.getcolor(opts.grid_background_color, "RGBA")
    grid = Image.new("RGBA", size=(params.cols * w, params.rows * h), color=grid_background_color)

    for i, img in enumerate(params.imgs):
        img_w, img_h = img.size
        w_offset, h_offset = 0 if img_w == w else (w - img_w) // 2, 0 if img_h == h else (h - img_h) // 2
        grid.paste(img, box=(i % params.cols * w + w_offset, i // params.cols * h + h_offset))

    return grid


class Grid(namedtuple("_Grid", ["tiles", "tile_w", "tile_h", "image_w", "image_h", "overlap"])):
    @property
    def tile_count(self) -> int:
        """
        The total number of tiles in the grid.
        """
        return sum(len(row[2]) for row in self.tiles)


def split_grid(image: Image.Image, tile_w: int = 512, tile_h: int = 512, overlap: int = 64) -> Grid:
    w, h = image.size

    non_overlap_width = tile_w - overlap
    non_overlap_height = tile_h - overlap

    cols = math.ceil((w - overlap) / non_overlap_width)
    rows = math.ceil((h - overlap) / non_overlap_height)

    dx = (w - tile_w) / (cols - 1) if cols > 1 else 0
    dy = (h - tile_h) / (rows - 1) if rows > 1 else 0

    grid = Grid([], tile_w, tile_h, w, h, overlap)
    for row in range(rows):
        row_images = []

        y = int(row * dy)

        if y + tile_h >= h:
            y = h - tile_h

        for col in range(cols):
            x = int(col * dx)

            if x + tile_w >= w:
                x = w - tile_w

            tile = image.crop((x, y, x + tile_w, y + tile_h))

            row_images.append([x, tile_w, tile])

        grid.tiles.append([y, tile_h, row_images])

    return grid


def combine_grid(grid):
    def make_mask_image(r):
        r = r * 255 / grid.overlap
        r = r.astype(np.uint8)
        return Image.fromarray(r, "L")

    mask_w = make_mask_image(np.arange(grid.overlap, dtype=np.float32).reshape((1, grid.overlap)).repeat(grid.tile_h, axis=0))
    mask_h = make_mask_image(np.arange(grid.overlap, dtype=np.float32).reshape((grid.overlap, 1)).repeat(grid.image_w, axis=1))

    combined_image = Image.new("RGB", (grid.image_w, grid.image_h))
    for y, h, row in grid.tiles:
        combined_row = Image.new("RGB", (grid.image_w, h))
        for x, w, tile in row:
            if x == 0:
                combined_row.paste(tile, (0, 0))
                continue

            combined_row.paste(tile.crop((0, 0, grid.overlap, h)), (x, 0), mask=mask_w)
            combined_row.paste(tile.crop((grid.overlap, 0, w, h)), (x + grid.overlap, 0))

        if y == 0:
            combined_image.paste(combined_row, (0, 0))
            continue

        combined_image.paste(combined_row.crop((0, 0, combined_row.width, grid.overlap)), (0, y), mask=mask_h)
        combined_image.paste(combined_row.crop((0, grid.overlap, combined_row.width, h)), (0, y + grid.overlap))

    return combined_image


class GridAnnotation:
    def __init__(self, text="", is_active=True):
        self.text = text
        self.is_active = is_active
        self.size = None


def draw_grid_annotations(im, width, height, hor_texts, ver_texts, margin=0):

    color_active = ImageColor.getcolor(opts.grid_text_active_color, "RGB")
    color_inactive = ImageColor.getcolor(opts.grid_text_inactive_color, "RGB")
    color_background = ImageColor.getcolor(opts.grid_background_color, "RGB")

    def wrap(drawing, text, font, line_length):
        lines = [""]
        for word in text.split():
            line = f"{lines[-1]} {word}".strip()
            if drawing.textlength(line, font=font) <= line_length:
                lines[-1] = line
            else:
                lines.append(word)
        return lines

    def draw_texts(drawing, draw_x, draw_y, lines, initial_fnt, initial_fontsize):
        for line in lines:
            fnt = initial_fnt
            fontsize = initial_fontsize
            while True:
                bbox = drawing.multiline_textbbox((0, 0), line.text, font=fnt)
                if (bbox[2] - bbox[0]) > line.allowed_width and fontsize > 0:
                    fontsize -= 1
                    fnt = get_font(fontsize)
                else:
                    break
            drawing.multiline_text((draw_x, draw_y + line.size[1] / 2), line.text, font=fnt, fill=color_active if line.is_active else color_inactive, anchor="mm", align="center")

            if not line.is_active:
                drawing.line((draw_x - line.size[0] // 2, draw_y + line.size[1] // 2, draw_x + line.size[0] // 2, draw_y + line.size[1] // 2), fill=color_inactive, width=4)

            draw_y += line.size[1] + line_spacing

    fontsize = (width + height) // 25
    line_spacing = fontsize // 2

    fnt = get_font(fontsize)

    pad_left = 0 if sum([sum([len(line.text) for line in lines]) for lines in ver_texts]) == 0 else width * 3 // 4

    cols = im.width // width
    rows = im.height // height

    assert cols == len(hor_texts), f"bad number of horizontal texts: {len(hor_texts)}; must be {cols}"
    assert rows == len(ver_texts), f"bad number of vertical texts: {len(ver_texts)}; must be {rows}"

    calc_img = Image.new("RGB", (1, 1), color_background)
    calc_d = ImageDraw.Draw(calc_img)

    for texts, allowed_width in zip(hor_texts + ver_texts, [width] * len(hor_texts) + [pad_left] * len(ver_texts)):
        items = [] + texts
        texts.clear()

        for line in items:
            wrapped = wrap(calc_d, line.text, fnt, allowed_width)
            texts += [GridAnnotation(x, line.is_active) for x in wrapped]

        for line in texts:
            bbox = calc_d.multiline_textbbox((0, 0), line.text, font=fnt)
            line.size = (bbox[2] - bbox[0], bbox[3] - bbox[1])
            line.allowed_width = allowed_width

    hor_text_heights = [sum([line.size[1] + line_spacing for line in lines]) - line_spacing for lines in hor_texts]
    ver_text_heights = [sum([line.size[1] + line_spacing for line in lines]) - line_spacing * len(lines) for lines in ver_texts]

    pad_top = 0 if sum(hor_text_heights) == 0 else max(hor_text_heights) + line_spacing * 2

    result = Image.new("RGB", (im.width + pad_left + margin * (cols - 1), im.height + pad_top + margin * (rows - 1)), color_background)

    for row in range(rows):
        for col in range(cols):
            cell = im.crop((width * col, height * row, width * (col + 1), height * (row + 1)))
            result.paste(cell, (pad_left + (width + margin) * col, pad_top + (height + margin) * row))

    d = ImageDraw.Draw(result)

    for col in range(cols):
        x = pad_left + (width + margin) * col + width / 2
        y = pad_top / 2 - hor_text_heights[col] / 2

        draw_texts(d, x, y, hor_texts[col], fnt, fontsize)

    for row in range(rows):
        x = pad_left / 2
        y = pad_top + (height + margin) * row + height / 2 - ver_text_heights[row] / 2

        draw_texts(d, x, y, ver_texts[row], fnt, fontsize)

    return result


def draw_prompt_matrix(im, width, height, all_prompts, margin=0):
    prompts = all_prompts[1:]
    boundary = math.ceil(len(prompts) / 2)

    prompts_horiz = prompts[:boundary]
    prompts_vert = prompts[boundary:]

    hor_texts = [[GridAnnotation(x, is_active=pos & (1 << i) != 0) for i, x in enumerate(prompts_horiz)] for pos in range(1 << len(prompts_horiz))]
    ver_texts = [[GridAnnotation(x, is_active=pos & (1 << i) != 0) for i, x in enumerate(prompts_vert)] for pos in range(1 << len(prompts_vert))]

    return draw_grid_annotations(im, width, height, hor_texts, ver_texts, margin)


def resize_image(resize_mode, im, width, height, upscaler_name=None, force_RGBA=False):
    """
    Resizes an image with the specified resize_mode, width, and height.

    Args:
        resize_mode: The mode to use when resizing the image.
            0: Resize the image to the specified width and height.
            1: Resize the image to fill the specified width and height, maintaining the aspect ratio, and then center the image within the dimensions, cropping the excess.
            2: Resize the image to fit within the specified width and height, maintaining the aspect ratio, and then center the image within the dimensions, filling empty with data from image.
        im: The image to resize.
        width: The width to resize the image to.
        height: The height to resize the image to.
        upscaler_name: The name of the upscaler to use. If not provided, defaults to opts.upscaler_for_img2img.
    """

    if not force_RGBA and im.mode == "RGBA":
        im = im.convert("RGB")

    upscaler_name = upscaler_name or opts.upscaler_for_img2img

    def resize(im, w, h):
        if upscaler_name is None or upscaler_name == "None" or im.mode == "L" or force_RGBA:
            return im.resize((w, h), resample=LANCZOS)

        scale = max(w / im.width, h / im.height)

        if scale > 1.0:
            upscalers = [x for x in shared.sd_upscalers if x.name == upscaler_name]
            if len(upscalers) == 0:
                upscaler = shared.sd_upscalers[0]
                print(f"could not find upscaler named {upscaler_name or '<empty string>'}, using {upscaler.name} as a fallback")
            else:
                upscaler = upscalers[0]

            im = upscaler.scaler.upscale(im, scale, upscaler.data_path)

        if im.width != w or im.height != h:
            im = im.resize((w, h), resample=LANCZOS)

        return im

    if resize_mode == 0:
        res = resize(im, width, height)

    elif resize_mode == 1:
        ratio = width / height
        src_ratio = im.width / im.height

        src_w = width if ratio > src_ratio else im.width * height // im.height
        src_h = height if ratio <= src_ratio else im.height * width // im.width

        resized = resize(im, src_w, src_h)
        res = Image.new("RGB" if not force_RGBA else "RGBA", (width, height))
        res.paste(resized, box=(width // 2 - src_w // 2, height // 2 - src_h // 2))

    else:
        ratio = width / height
        src_ratio = im.width / im.height

        src_w = width if ratio < src_ratio else im.width * height // im.height
        src_h = height if ratio >= src_ratio else im.height * width // im.width

        resized = resize(im, src_w, src_h)
        res = Image.new("RGB" if not force_RGBA else "RGBA", (width, height))
        res.paste(resized, box=(width // 2 - src_w // 2, height // 2 - src_h // 2))

        if ratio < src_ratio:
            fill_height = height // 2 - src_h // 2
            if fill_height > 0:
                res.paste(resized.resize((width, fill_height), box=(0, 0, width, 0)), box=(0, 0))
                res.paste(resized.resize((width, fill_height), box=(0, resized.height, width, resized.height)), box=(0, fill_height + src_h))
        elif ratio > src_ratio:
            fill_width = width // 2 - src_w // 2
            if fill_width > 0:
                res.paste(resized.resize((fill_width, height), box=(0, 0, 0, height)), box=(0, 0))
                res.paste(resized.resize((fill_width, height), box=(resized.width, 0, resized.width, height)), box=(fill_width + src_w, 0))

    return res


if not shared.cmd_opts.unix_filenames_sanitization:
    invalid_filename_chars = '#<>:"/\\|?*\n\r\t'
else:
    invalid_filename_chars = "/"
invalid_filename_prefix = " "
invalid_filename_postfix = " ."
re_nonletters = re.compile(r"[\s" + string.punctuation + "]+")
re_pattern = re.compile(r"(.*?)(?:\[([^\[\]]+)\]|$)")
re_pattern_arg = re.compile(r"(.*)<([^>]*)>$")
max_filename_part_length = shared.cmd_opts.filenames_max_length
NOTHING_AND_SKIP_PREVIOUS_TEXT = object()


def sanitize_filename_part(text, replace_spaces=True):
    if text is None:
        return None

    if replace_spaces:
        text = text.replace(" ", "_")

    text = text.translate({ord(x): "_" for x in invalid_filename_chars})
    text = text.lstrip(invalid_filename_prefix)[:max_filename_part_length]
    text = text.rstrip(invalid_filename_postfix)
    return text


@functools.cache
def get_scheduler_str(sampler_name, scheduler_name):
    """Returns {Scheduler} if the scheduler is applicable to the sampler"""
    if scheduler_name == "Automatic":
        config = sd_samplers.find_sampler_config(sampler_name)
        scheduler_name = config.options.get("scheduler", "Automatic")
    return scheduler_name.capitalize()


@functools.cache
def get_sampler_scheduler_str(sampler_name, scheduler_name):
    """Returns the '{Sampler} {Scheduler}' if the scheduler is applicable to the sampler"""
    return f"{sampler_name} {get_scheduler_str(sampler_name, scheduler_name)}"


def get_sampler_scheduler(p, sampler):
    """Returns '{Sampler} {Scheduler}' / '{Scheduler}' / 'NOTHING_AND_SKIP_PREVIOUS_TEXT'"""
    if hasattr(p, "scheduler") and hasattr(p, "sampler_name"):
        if sampler:
            sampler_scheduler = get_sampler_scheduler_str(p.sampler_name, p.scheduler)
        else:
            sampler_scheduler = get_scheduler_str(p.sampler_name, p.scheduler)
        return sanitize_filename_part(sampler_scheduler, replace_spaces=False)
    return NOTHING_AND_SKIP_PREVIOUS_TEXT


class FilenameGenerator:
    replacements = {
        "basename": lambda self: self.basename or "img",
        "seed": lambda self: self.seed if self.seed is not None else "",
        "seed_first": lambda self: self.seed if self.p.batch_size == 1 else self.p.all_seeds[0],
        "seed_last": lambda self: NOTHING_AND_SKIP_PREVIOUS_TEXT if self.p.batch_size == 1 else self.p.all_seeds[-1],
        "steps": lambda self: self.p and self.p.steps,
        "cfg": lambda self: self.p and self.p.cfg_scale,
        "dcfg": lambda self: self.p and self.p.distilled_cfg_scale,
        "shift": lambda self: self.p and self.p.distilled_cfg_scale,
        "elapsed_time": lambda self: int(time.time() - shared.state.time_start),
        "width": lambda self: self.image.width,
        "height": lambda self: self.image.height,
        "styles": lambda self: self.p and sanitize_filename_part(", ".join([style for style in self.p.styles if not style == "None"]) or "None", replace_spaces=False),
        "sampler": lambda self: self.p and sanitize_filename_part(self.p.sampler_name, replace_spaces=False),
        "sampler_scheduler": lambda self: self.p and get_sampler_scheduler(self.p, True),
        "scheduler": lambda self: self.p and get_sampler_scheduler(self.p, False),
        "model_hash": lambda self: getattr(self.p, "sd_model_hash", shared.sd_model.sd_model_hash),
        "model_name": lambda self: sanitize_filename_part(shared.sd_model.sd_checkpoint_info.name_for_extra, replace_spaces=False),
        "date": lambda self: datetime.datetime.now().strftime("%Y-%m-%d"),
        "datetime": lambda self, *args: self.datetime(*args),  # accepts formats: [datetime], [datetime<Format>], [datetime<Format><Time Zone>]
        "job_timestamp": lambda self: getattr(self.p, "job_timestamp", shared.state.job_timestamp),
        "prompt_hash": lambda self, *args: self.string_hash(self.prompt, *args),
        "negative_prompt_hash": lambda self, *args: self.string_hash(self.p.negative_prompt, *args),
        "full_prompt_hash": lambda self, *args: self.string_hash(f"{self.p.prompt} {self.p.negative_prompt}", *args),  # a space in between to create a unique string
        "prompt": lambda self: sanitize_filename_part(self.prompt),
        "prompt_no_styles": lambda self: self.prompt_no_style(),
        "prompt_spaces": lambda self: sanitize_filename_part(self.prompt, replace_spaces=False),
        "prompt_words": lambda self: self.prompt_words(),
        "batch_number": lambda self: NOTHING_AND_SKIP_PREVIOUS_TEXT if self.p.batch_size == 1 or self.zip else self.p.batch_index + 1,
        "batch_size": lambda self: self.p.batch_size,
        "generation_number": lambda self: NOTHING_AND_SKIP_PREVIOUS_TEXT if (self.p.n_iter == 1 and self.p.batch_size == 1) or self.zip else self.p.iteration * self.p.batch_size + self.p.batch_index + 1,
        "hasprompt": lambda self, *args: self.hasprompt(*args),  # accepts formats:[hasprompt<prompt1|default><prompt2>..]
        "clip_skip": lambda self: opts.data["CLIP_stop_at_last_layers"],
        "denoising": lambda self: self.p.denoising_strength if self.p and self.p.denoising_strength else NOTHING_AND_SKIP_PREVIOUS_TEXT,
        "user": lambda self: self.p.user,
        "vae_filename": lambda self: self.get_vae_filename(),
        "none": lambda self: "",  # Overrides the default, so you can get just the sequence number
        "image_hash": lambda self, *args: self.image_hash(*args),  # accepts formats: [image_hash<length>] default full hash
    }
    default_time_format = "%Y%m%d%H%M%S"

    def __init__(self, p, seed, prompt, image, zip=False, basename=""):
        self.p = p
        self.seed = seed
        self.prompt = prompt
        self.image = image
        self.zip = zip
        self.basename = basename

    def get_vae_filename(self):
        """Get the name of the VAE file."""

        import modules.sd_vae as sd_vae

        if sd_vae.loaded_vae_file is None:
            return "NoneType"

        file_name = os.path.basename(sd_vae.loaded_vae_file)
        split_file_name = file_name.split(".")
        if len(split_file_name) > 1 and split_file_name[0] == "":
            return split_file_name[1]  # if the first character of the filename is "." then [1] is obtained.
        else:
            return split_file_name[0]

    def hasprompt(self, *args):
        lower = self.prompt.lower()
        if self.p is None or self.prompt is None:
            return None
        outres = ""
        for arg in args:
            if arg != "":
                division = arg.split("|")
                expected = division[0].lower()
                default = division[1] if len(division) > 1 else ""
                if lower.find(expected) >= 0:
                    outres = f"{outres}{expected}"
                else:
                    outres = outres if default == "" else f"{outres}{default}"
        return sanitize_filename_part(outres)

    def prompt_no_style(self):
        if self.p is None or self.prompt is None:
            return None

        prompt_no_style = self.prompt
        for style in shared.prompt_styles.get_style_prompts(self.p.styles):
            if style:
                for part in style.split("{prompt}"):
                    prompt_no_style = prompt_no_style.replace(part, "").replace(", ,", ",").strip().strip(",")

                prompt_no_style = prompt_no_style.replace(style, "").strip().strip(",").strip()

        return sanitize_filename_part(prompt_no_style, replace_spaces=False)

    def prompt_words(self):
        words = [x for x in re_nonletters.split(self.prompt or "") if x]
        if len(words) == 0:
            words = ["empty"]
        return sanitize_filename_part(" ".join(words[0 : opts.directories_max_prompt_words]), replace_spaces=False)

    def datetime(self, *args):
        time_datetime = datetime.datetime.now()

        time_format = args[0] if (args and args[0] != "") else self.default_time_format
        try:
            time_zone = pytz.timezone(args[1]) if len(args) > 1 else None
        except pytz.exceptions.UnknownTimeZoneError:
            time_zone = None

        time_zone_time = time_datetime.astimezone(time_zone)
        try:
            formatted_time = time_zone_time.strftime(time_format)
        except (ValueError, TypeError):
            formatted_time = time_zone_time.strftime(self.default_time_format)

        return sanitize_filename_part(formatted_time, replace_spaces=False)

    def image_hash(self, *args):
        length = int(args[0]) if (args and args[0] != "") else None
        return hashlib.sha256(self.image.tobytes()).hexdigest()[0:length]

    def string_hash(self, text, *args):
        length = int(args[0]) if (args and args[0] != "") else 8
        return hashlib.sha256(text.encode()).hexdigest()[0:length]

    def apply(self, x):
        res = ""

        for m in re_pattern.finditer(x):
            text, pattern = m.groups()

            if pattern is None:
                res += text
                continue

            pattern_args = []
            while True:
                m = re_pattern_arg.match(pattern)
                if m is None:
                    break

                pattern, arg = m.groups()
                pattern_args.insert(0, arg)

            fun = self.replacements.get(pattern.lower())
            if fun is not None:
                try:
                    replacement = fun(self, *pattern_args)
                except Exception:
                    replacement = None
                    errors.report(f"Error adding [{pattern}] to filename", exc_info=True)

                if replacement == NOTHING_AND_SKIP_PREVIOUS_TEXT:
                    continue
                elif replacement is not None:
                    res += text + str(replacement)
                    continue

            res += f"{text}[{pattern}]"

        return res


def get_next_sequence_number(path, basename):
    """
    Determines and returns the next sequence number to use when saving an image in the specified directory.

    The sequence starts at 0.
    """
    result = -1
    if basename != "":
        basename = f"{basename}-"

    prefix_length = len(basename)
    for p in os.listdir(path):
        if p.startswith(basename):
            parts = os.path.splitext(p[prefix_length:])[0].split("-")  # splits the filename (removing the basename first if one is defined, so the sequence number is always the first element)
            try:
                result = max(int(parts[0]), result)
            except ValueError:
                pass

    return result + 1


def save_image_with_geninfo(image, geninfo, filename, extension=None, existing_pnginfo=None, pnginfo_section_name="parameters"):
    """
    Saves image to filename, including geninfo as text information for generation info.
    For PNG images, geninfo is added to existing pnginfo dictionary using the pnginfo_section_name argument as key.
    For JPG images, there's no dictionary and geninfo just replaces the EXIF description.
    """

    if extension is None:
        extension = os.path.splitext(filename)[1]

    image_format = Image.registered_extensions()[extension]

    if extension.lower() == ".png":
        existing_pnginfo = existing_pnginfo or {}
        if opts.enable_pnginfo:
            existing_pnginfo[pnginfo_section_name] = geninfo
            pnginfo_data = PngImagePlugin.PngInfo()
            for k, v in (existing_pnginfo or {}).items():
                pnginfo_data.add_text(k, str(v))
        else:
            pnginfo_data = None

        image.save(filename, format=image_format, quality=opts.jpeg_quality, pnginfo=pnginfo_data)

    elif extension.lower() in (".jpg", ".jpeg", ".webp"):
        if image.mode == "RGBA":
            image = image.convert("RGB")
        elif image.mode == "I;16":
            image = image.point(lambda p: p * 0.0038910505836576).convert("RGB" if extension.lower() == ".webp" else "L")

        image.save(filename, format=image_format, quality=opts.jpeg_quality, lossless=opts.webp_lossless)

        if opts.enable_pnginfo and geninfo is not None:
            exif_bytes = piexif.dump(
                {
                    "Exif": {piexif.ExifIFD.UserComment: piexif.helper.UserComment.dump(geninfo or "", encoding="unicode")},
                }
            )

            piexif.insert(exif_bytes, filename)
    elif extension.lower() in (".avif", ".jxl"):
        if opts.enable_pnginfo and geninfo is not None:
            exif_bytes = piexif.dump(
                {
                    "Exif": {piexif.ExifIFD.UserComment: piexif.helper.UserComment.dump(geninfo or "", encoding="unicode")},
                }
            )
        else:
            exif_bytes = None

        image.save(filename, format=image_format, quality=opts.jpeg_quality, exif=exif_bytes)
    elif extension.lower() == ".gif":
        image.save(filename, format=image_format, comment=geninfo)
    else:
        image.save(filename, format=image_format, quality=opts.jpeg_quality)


def save_image(image, path, basename, seed=None, prompt=None, extension="png", info=None, short_filename=False, no_prompt=False, grid=False, pnginfo_section_name="parameters", p=None, existing_info=None, forced_filename=None, suffix="", save_to_dirs=None):
    """Save an image.

    Args:
        image (`PIL.Image`):
            The image to be saved.
        path (`str`):
            The directory to save the image. Note, the option `save_to_dirs` will make the image to be saved into a sub directory.
        basename (`str`):
            The base filename which will be applied to `filename pattern`.
        seed, prompt, short_filename,
        extension (`str`):
            Image file extension, default is `png`.
        pngsectionname (`str`):
            Specify the name of the section which `info` will be saved in.
        info (`str` or `PngImagePlugin.iTXt`):
            PNG info chunks.
        existing_info (`dict`):
            Additional PNG info. `existing_info == {pngsectionname: info, ...}`
        no_prompt:
            TODO I don't know its meaning.
        p (`StableDiffusionProcessing` or `Processing`)
        forced_filename (`str`):
            If specified, `basename` and filename pattern will be ignored.
        save_to_dirs (bool):
            If true, the image will be saved into a subdirectory of `path`.

    Returns: (fullfn, txt_fullfn)
        fullfn (`str`):
            The full path of the saved imaged.
        txt_fullfn (`str` or None):
            If a text file is saved for this image, this will be its full path. Otherwise None.
    """
    namegen = FilenameGenerator(p, seed, prompt, image, basename=basename)

    # WebP and JPG formats have maximum dimension limits of 16383 and 65535 respectively. switch to PNG which has a much higher limit
    if (image.height > 65535 or image.width > 65535) and extension.lower() in ("jpg", "jpeg") or (image.height > 16383 or image.width > 16383) and extension.lower() == "webp":
        print("Image dimensions too large; saving as PNG")
        extension = "png"

    if save_to_dirs is None:
        save_to_dirs = (grid and opts.grid_save_to_dirs) or (not grid and opts.save_to_dirs and not no_prompt)

    if save_to_dirs:
        dirname = namegen.apply(opts.directories_filename_pattern or "[prompt_words]").lstrip(" ").rstrip("\\ /")
        path = os.path.join(path, dirname)

    os.makedirs(path, exist_ok=True)

    if forced_filename is None:
        if short_filename or seed is None:
            file_decoration = ""
        elif hasattr(p, "override_settings"):
            file_decoration = p.override_settings.get("samples_filename_pattern")
        else:
            file_decoration = None

        if file_decoration is None:
            file_decoration = opts.samples_filename_pattern or ("[seed]" if opts.save_to_dirs else "[seed]-[prompt_spaces]")

        file_decoration = namegen.apply(file_decoration) + suffix

        add_number = opts.save_images_add_number or file_decoration == ""

        if file_decoration != "" and add_number:
            file_decoration = f"-{file_decoration}"

        if add_number:
            basecount = get_next_sequence_number(path, basename)
            fullfn = None
            for i in range(500):
                fn = f"{basecount + i:05}" if basename == "" else f"{basename}-{basecount + i:04}"
                fullfn = os.path.join(path, f"{fn}{file_decoration}.{extension}")
                if not os.path.exists(fullfn):
                    break
        else:
            fullfn = os.path.join(path, f"{file_decoration}.{extension}")
    else:
        fullfn = os.path.join(path, f"{forced_filename}.{extension}")

    pnginfo = existing_info or {}
    if info is not None:
        pnginfo[pnginfo_section_name] = info

    params = script_callbacks.ImageSaveParams(image, p, fullfn, pnginfo)
    script_callbacks.before_image_saved_callback(params)

    image = params.image
    fullfn = params.filename
    info = params.pnginfo.get(pnginfo_section_name, None)

    def _atomically_save_image(image_to_save, filename_without_extension, extension):
        """
        save image with .tmp extension to avoid race condition when another process detects new image in the directory
        """
        temp_file_path = f"{filename_without_extension}.tmp"

        save_image_with_geninfo(image_to_save, info, temp_file_path, extension, existing_pnginfo=params.pnginfo, pnginfo_section_name=pnginfo_section_name)

        filename = filename_without_extension + extension
        without_extension = filename_without_extension
        if shared.opts.save_images_replace_action != "Override":
            n = 0
            while os.path.exists(filename):
                n += 1
                without_extension = f"{filename_without_extension}-{n}"
                filename = without_extension + extension
        os.replace(temp_file_path, filename)
        return without_extension

    fullfn_without_extension, extension = os.path.splitext(params.filename)
    if hasattr(os, "statvfs"):
        max_name_len = os.statvfs(path).f_namemax
        fullfn_without_extension = fullfn_without_extension[: max_name_len - max(4, len(extension))]
        params.filename = fullfn_without_extension + extension
        fullfn = params.filename

    fullfn_without_extension = _atomically_save_image(image, fullfn_without_extension, extension)
    fullfn = fullfn_without_extension + extension
    image.already_saved_as = fullfn

    oversize = image.width > opts.target_side_length or image.height > opts.target_side_length
    if opts.export_for_4chan and (oversize or os.stat(fullfn).st_size > opts.img_downscale_threshold * 1024 * 1024):
        ratio = image.width / image.height
        resize_to = None
        if oversize and ratio > 1:
            resize_to = round(opts.target_side_length), round(image.height * opts.target_side_length / image.width)
        elif oversize:
            resize_to = round(image.width * opts.target_side_length / image.height), round(opts.target_side_length)

        if resize_to is not None:
            try:
                # Resizing image with LANCZOS could throw an exception if e.g. image mode is I;16
                image = image.resize(resize_to, LANCZOS)
            except Exception:
                image = image.resize(resize_to)
        try:
            _ = _atomically_save_image(image, fullfn_without_extension, ".jpg")
        except Exception as e:
            errors.display(e, "saving image as downscaled JPG")

    if opts.save_txt and info is not None:
        txt_fullfn = f"{fullfn_without_extension}.txt"
        with open(txt_fullfn, "w", encoding="utf8") as file:
            file.write(f"{info}\n")
    else:
        txt_fullfn = None

    script_callbacks.image_saved_callback(params)

    return fullfn, txt_fullfn


IGNORED_INFO_KEYS = {
    "jfif",
    "jfif_version",
    "jfif_unit",
    "jfif_density",
    "dpi",
    "exif",
    "loop",
    "background",
    "timestamp",
    "duration",
    "progressive",
    "progression",
    "icc_profile",
    "chromaticity",
    "photoshop",
}


# =====================================================================
# Multi-format generation-metadata extraction
#
# Ported from the standalone epd.py metadata extractor. Only the pure
# parsing/detection logic is brought over: format autodetection for
# A1111/Forge, ComfyUI, NovelAI, SwarmUI, Fooocus, InvokeAI, DrawThings,
# Midjourney, DAVANT and CivitAI-tagged metadata. None of epd.py's
# CivitAI API/hash-lookup, caching, file-routing, or CLI/batch-pipeline
# code is included here; this is single in-memory-image metadata
# extraction only.
# =====================================================================

NEGATIVE_PREFIX = "Negative prompt: "
PARAMS_PREFIX = "Steps: "

METADATA_FORMATS = [
    {
        "name": "ComfyUI",
        "png_fields": ["prompt", "workflow", "generation_data"],
        "jpg_fields": ["user_comment"],
        "webp_fields": ["user_comment"],
        "regex": re.compile(r"class_type"),
        "is_json": True,
    },
    {
        "name": "ComfyUI",
        "webp_fields": ["camera_manufacturer", "image_descriptiion"],
        "regex": re.compile(r"class_type"),
        "is_json": True,
    },
    {
        "name": "CivitAI",
        "png_fields": ["parameters"],
        "jpg_fields": ["user_comment"],
        "regex": re.compile(r"(C|c)ivitai"),
    },
    {
        "name": "DAVANT",
        "png_fields": ["parameters", "davant__batch_parameters"],
        "regex": re.compile(r"(S|s)teps:"),
        "verify_fields": True,
    },
    {
        "name": "DrawThings",
        "png_fields": ["description", "usercomment", "creatortool"],
        "regex": re.compile(r"(S|s)teps:"),
        "is_xmp": True,
    },
    {
        "name": "Fooocus",
        "png_fields": ["parameters", "fooocus_scheme"],
        "jpg_fields": ["user_comment"],
        "regex": re.compile(r"Fooocus"),
    },
    {
        "name": "InvokeAI",
        "png_fields": ["invokeai_metadata", "invokeai_graph"],
    },
    {
        "name": "Midjourney",
        "png_fields": ["description"],
        "regex": re.compile(r"^(?=.*--(?:ar|v|q|quality|style|chaos|seed|stop)\b)[\s\S]+$", re.IGNORECASE),
    },
    {
        "name": "NovelAI",
        "png_fields": ["comment", "description", "title", "software", "source"],
        "is_json": True,
    },
    {
        "name": "NovelAI",
        "jpg_fields": ["comment", "user_comment"],
        "is_json": True,
    },
    {
        "name": "SwarmUI",
        "png_fields": ["parameters"],
        "jpg_fields": ["user_comment"],
        "regex": re.compile(r"sui_image_params"),
        "is_json": True,
    },
    {
        "name": "A1111",
        "png_fields": ["usercomment"],
        "regex": re.compile(r"(S|s)teps:"),
        "is_json": False,
    },
    {
        "name": "A1111",
        "png_fields": ["parameters"],
        "jpg_fields": ["user_comment"],
        "webp_fields": ["user_comment"],
        "regex": re.compile(r"(S|s)teps:"),
        "is_json": False,
    },
]


def _gi_safe_json_parse(text):
    """Safely parse JSON text, handling NaN values."""
    if not text or not isinstance(text, str):
        return None
    try:
        safe_text = re.sub(r"\bNaN\b", "null", text)
        return json.loads(safe_text)
    except Exception:
        return None


def _gi_try_parse_json_inside_text(text):
    """Try to find and parse a JSON object inside a text string."""
    if not text or not isinstance(text, str):
        return None
    first = text.find("{")
    if first == -1:
        return None
    for end_offset in [1000, 5000, len(text) - first]:
        end = min(first + end_offset, len(text))
        candidate = text[first:end]
        last_brace = candidate.rfind("}")
        if last_brace != -1:
            candidate = candidate[:last_brace + 1]
            parsed = _gi_safe_json_parse(candidate)
            if parsed and isinstance(parsed, dict):
                return parsed
    return None


def _gi_parse_value(value):
    """Parse a value that might be JSON or plain text."""
    value = value.strip()
    if not value:
        return value
    if (value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]")):
        parsed = _gi_safe_json_parse(value)
        if parsed is not None:
            return parsed
    return value


def _gi_parse_key_value_pairs(input_str):
    """Parse key-value pairs from an A1111-style parameters string."""
    if not input_str:
        return {}

    result = {}
    current_key = ""
    current_value = ""
    in_quotes = False
    in_braces = 0
    in_brackets = 0
    is_parsing_key = True

    i = 0
    while i < len(input_str):
        char = input_str[i]
        if char == '"' and (i == 0 or input_str[i - 1] != "\\"):
            in_quotes = not in_quotes
        elif not in_quotes:
            if char == "{":
                in_braces += 1
            elif char == "}":
                in_braces -= 1
            elif char == "[":
                in_brackets += 1
            elif char == "]":
                in_brackets -= 1
        if char == ":" and is_parsing_key and not in_quotes and in_braces == 0 and in_brackets == 0:
            is_parsing_key = False
            i += 1
            continue
        if char == "," and not in_quotes and in_braces == 0 and in_brackets == 0:
            if current_key:
                result[current_key.strip()] = _gi_parse_value(current_value.strip())
            current_key = ""
            current_value = ""
            is_parsing_key = True
            i += 1
            continue
        if is_parsing_key:
            current_key += char
        else:
            current_value += char
        i += 1

    if current_key:
        result[current_key.strip()] = _gi_parse_value(current_value.strip())
    return result


def _gi_get_a1111_metadata(metadata_string):
    """Parse A1111/Forge format metadata."""
    if not metadata_string:
        return {}

    parts = []
    current_part = ""

    lines = metadata_string.split("\n")

    for line in lines:
        if line.startswith(NEGATIVE_PREFIX.strip()):
            if current_part:
                parts.append(current_part.strip())
            parts.append(line)
            current_part = ""
        elif line.startswith(PARAMS_PREFIX):
            if current_part:
                parts.append(current_part.strip())
            parts.append(line)
            current_part = ""
        else:
            current_part += line + "\n"

    if current_part.strip():
        parts.append(current_part.strip())

    result = {"prompt": "", "negative": "", "extra": ""}

    for part in parts:
        if part.startswith(PARAMS_PREFIX):
            result["extra"] = part
        elif part.startswith(NEGATIVE_PREFIX.strip()):
            result["negative"] = part[len(NEGATIVE_PREFIX):].strip()
        else:
            if result["prompt"]:
                result["prompt"] += "\n" + part
            else:
                result["prompt"] = part

    return result


def _gi_get_novelai_metadata(metadata_string):
    """Parse NovelAI format metadata (JSON)."""
    if not metadata_string:
        return None

    parsed = _gi_safe_json_parse(metadata_string)

    if not parsed or not isinstance(parsed, dict):
        return None

    if not any(key in parsed for key in ["prompt", "uc", "steps", "sampler"]):
        return None

    result = {
        "prompt": parsed.get("prompt", ""),
        "negative": parsed.get("uc", ""),
        "steps": parsed.get("steps"),
        "sampler": parsed.get("sampler"),
        "cfg_scale": parsed.get("scale"),
        "seed": parsed.get("seed"),
        "width": parsed.get("width"),
        "height": parsed.get("height"),
    }

    extra_parts = []
    if result["steps"]:
        extra_parts.append(f"Steps: {result['steps']}")
    if result["sampler"]:
        extra_parts.append(f"Sampler: {result['sampler']}")
    if result["cfg_scale"]:
        extra_parts.append(f"CFG scale: {result['cfg_scale']}")
    if result["seed"]:
        extra_parts.append(f"Seed: {result['seed']}")
    if result["width"] and result["height"]:
        extra_parts.append(f"Size: {result['width']}x{result['height']}")

    result["extra"] = ", ".join(extra_parts)

    return result


# A few ComfyUI sampler names don't translate to WebUI's k_-prefixed alias
# scheme by simple prefixing (e.g. ComfyUI's "dpm_adaptive" vs WebUI's alias
# "k_dpm_ad", or ComfyUI's "dpmpp_2s_ancestral" vs WebUI's "k_dpmpp_2s_a").
# Map ComfyUI's exact sampler_name strings straight to WebUI's lowercased
# display names (these are looked up directly, bypassing samplers_map).
_GI_COMFYUI_SAMPLER_OVERRIDES = {
    "dpm_2_ancestral": "dpm2 a",
    "dpm_adaptive": "dpm adaptive",
    "dpmpp_2s_ancestral": "dpm++ 2s a",
    "dpmpp_2m_sde_heun": "dpm++ 2m sde heun",
    "dpm_2": "dpm2",
}


def _gi_normalize_sampler_name(sampler_name):
    """Translate a ComfyUI-style sampler name (e.g. "euler_ancestral",
    "dpmpp_2m") into WebUI's display name (e.g. "Euler a", "DPM++ 2M").

    ComfyUI's KSampler uses bare k-diffusion function-style names with no
    prefix ("euler_ancestral", "dpmpp_sde"), while WebUI's samplers_map keys
    its aliases with a "k_" prefix ("k_euler_ancestral", "k_dpmpp_sde"), and
    a handful of samplers use irregular aliases that don't follow that
    pattern at all. This tries, in order: the bare name as-is (covers things
    like "ddim", "lcm", "euler" that happen to already match a WebUI name or
    alias), then with a "k_" prefix prepended, then a small override table
    for the known irregular cases, falling back to the original string
    unchanged if nothing matches so unrecognized/future sampler names aren't
    silently dropped — they simply won't auto-select in the UI.
    """
    if not sampler_name or not isinstance(sampler_name, str):
        return sampler_name

    candidate = sampler_name.strip().lower()
    if not candidate:
        return sampler_name

    try:
        mapped = sd_samplers.samplers_map.get(candidate)
        if not mapped:
            mapped = sd_samplers.samplers_map.get(f"k_{candidate}")
        if not mapped and candidate in _GI_COMFYUI_SAMPLER_OVERRIDES:
            mapped = sd_samplers.samplers_map.get(_GI_COMFYUI_SAMPLER_OVERRIDES[candidate])
    except Exception:
        mapped = None

    return mapped if mapped else sampler_name


def _gi_get_comfyui_metadata(workflow_data):
    """Extract metadata from a ComfyUI workflow graph."""
    if not isinstance(workflow_data, dict):
        return None

    negative_keywords = [
        "bad quality", "worst quality", "low quality", "bad anatomy", "lowres",
        "ugly", "deformed", "mutant", "mutated", "disfigured", "distorted",
        "censorship", "censored", "pixelated", "blurry", "blurred",
        "malformed", "extra limbs", "missing limbs", "poorly drawn",
        "gross proportions", "watermark", "signature", "text", "error",
        "cropped", "jpeg artifacts", "compression artifacts",
    ]

    def classify_by_meta_title(node):
        if not isinstance(node, dict):
            return None
        meta = node.get("_meta", {})
        if not isinstance(meta, dict):
            return None
        title = meta.get("title", "")
        if not isinstance(title, str):
            return None
        title_lower = title.lower()
        if "positive" in title_lower:
            return "positive"
        elif "negative" in title_lower:
            return "negative"
        return None

    def classify_by_field_name(field_name):
        field_lower = field_name.lower()
        if field_lower == "positive":
            return "positive"
        elif field_lower == "negative":
            return "negative"
        return None

    def classify_by_content(text):
        if not text or not isinstance(text, str):
            return None
        text_lower = text.lower()
        negative_matches = [kw for kw in negative_keywords if kw in text_lower]
        if len(negative_matches) >= 2:
            return "negative"
        return None

    def get_nodes_values(graph, class_regex, fields, check_widgets=False):
        results = {"positive": [], "negative": []}

        for node_id, node in graph.items():
            if not isinstance(node, dict):
                continue

            class_type = str(node.get("class_type") or node.get("type", "")).lower()
            if not class_regex.search(class_type):
                continue

            node_classification = classify_by_meta_title(node)

            inputs = node.get("inputs", {})
            if isinstance(inputs, dict):
                for field in fields:
                    value = inputs.get(field)
                    if isinstance(value, (str, int, float)) and str(value).strip():
                        value_str = str(value)

                        if len(value_str.strip()) < 10:
                            continue

                        classification = None

                        if node_classification:
                            classification = node_classification

                        if not classification:
                            classification = classify_by_field_name(field)

                        if not classification:
                            classification = classify_by_content(value_str)

                        if not classification:
                            classification = "positive"

                        results[classification].append(value_str)

            if check_widgets and "widgets_values" in node:
                widgets = node["widgets_values"]

                if isinstance(widgets, list):
                    for value in widgets:
                        if isinstance(value, str) and len(value) > 20:
                            if value.endswith(".safetensors") or value.endswith(".pt") or value.endswith(".ckpt"):
                                continue

                            if value in ["Baked VAE", "none", "comfy", "auto", "simple"]:
                                continue

                            classification = None

                            if node_classification:
                                classification = node_classification

                            if not classification:
                                classification = classify_by_content(value)

                            if not classification:
                                classification = "positive"

                            results[classification].append(value)

        return results

    def get_first(graph, class_regex, fields):
        for node_id, node in graph.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or node.get("type", "")).lower()
            if not class_regex.search(class_type):
                continue
            inputs = node.get("inputs", {})
            if isinstance(inputs, dict):
                for field in fields:
                    value = inputs.get(field)
                    if isinstance(value, (str, int, float)) and str(value).strip():
                        return str(value)
        return None

    prompt_results = get_nodes_values(
        workflow_data,
        re.compile(r"cliptextencode|wildcard|textbox|eff\. loader|efficient loader|ttn text|string variable", re.IGNORECASE),
        ["text", "positive", "negative", "wildcard_text", "clip_l", "t5xxl", "string"],
        check_widgets=True,
    )

    metadata = {
        "prompt": "\n".join(prompt_results["positive"]) if prompt_results["positive"] else "",
        "negative": "\n".join(prompt_results["negative"]) if prompt_results["negative"] else "",
        "steps": get_first(workflow_data, re.compile(r"scheduler|sampler|ksampler", re.IGNORECASE), ["steps"]),
        "sampler": _gi_normalize_sampler_name(get_first(workflow_data, re.compile(r"scheduler|sampler|ksampler", re.IGNORECASE), ["sampler_name"])),
        "scheduler": get_first(workflow_data, re.compile(r"scheduler|sampler|ksampler", re.IGNORECASE), ["scheduler"]),
        "cfg_scale": get_first(workflow_data, re.compile(r"guidance|sampler|ksampler|cliptextencode", re.IGNORECASE), ["guidance", "cfg"]),
        "seed": get_first(workflow_data, re.compile(r"randomnoise|sampler|ksampler|seed", re.IGNORECASE), ["noise_seed", "seed"]),
        "width": get_first(workflow_data, re.compile(r"latentimage|loader|efficient", re.IGNORECASE), ["width", "empty_latent_width"]),
        "height": get_first(workflow_data, re.compile(r"latentimage|loader|efficient", re.IGNORECASE), ["height", "empty_latent_height"]),
        "model": get_first(workflow_data, re.compile(r"checkpoint|loader|efficient", re.IGNORECASE), ["ckpt_name", "base_ckpt_name", "unet_name"]),
        "vae": get_first(workflow_data, re.compile(r"vae|loader|efficient", re.IGNORECASE), ["vae_name"]),
    }

    return metadata


def _gi_clean_prompt_text(text):
    """Remove embedding URNs and normalize whitespace/commas in a prompt string."""
    if not text or not isinstance(text, str):
        return text

    text = re.sub(r"embedding:urn:air:[^:]+:embedding:civitai:\d+@\d+", "", text)
    text = re.sub(r"\bembedding:\S+", "", text)
    text = re.sub(r",(\s*,)+", ",", text)
    text = re.sub(r",\s*\n\s*,", ",", text)
    text = re.sub(r"^\s*,+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*,+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    text = text.strip(",").strip()

    return text


def _gi_parse_extrametadata(extra_metadata_str):
    """Parse CivitAI's extraMetadata field (JSON string with prompt data)."""
    if not extra_metadata_str or not isinstance(extra_metadata_str, str):
        return None

    try:
        data = _gi_safe_json_parse(extra_metadata_str)

        if not data or not isinstance(data, dict):
            try:
                unescaped = json.loads(f'"{extra_metadata_str}"')
                data = _gi_safe_json_parse(unescaped)
            except Exception:
                pass

        if not data or not isinstance(data, dict):
            return None

        result = {
            "prompt": _gi_clean_prompt_text(data.get("prompt", "")),
            "negative": _gi_clean_prompt_text(data.get("negativePrompt", "")),
            "steps": data.get("steps"),
            "cfg_scale": data.get("cfgScale"),
            "sampler": data.get("sampler"),
            "seed": data.get("seed"),
        }

        return result

    except Exception:
        return None


def _gi_detect_comfyui_workflow(data):
    """Detect if data is a ComfyUI workflow and extract metadata from it."""
    if not isinstance(data, dict):
        return None

    nodes = data.get("nodes") or data.get("graph")

    if isinstance(nodes, list):
        nodes_dict = {}
        for node in nodes:
            if isinstance(node, dict) and "id" in node:
                nodes_dict[str(node["id"])] = node
        nodes = nodes_dict if nodes_dict else None

    if not nodes and isinstance(data, dict):
        node_count = sum(1 for v in data.values() if isinstance(v, dict) and ("class_type" in v or "type" in v))
        if node_count > 0:
            nodes = data

    if not nodes:
        for v in data.values():
            if isinstance(v, dict) and ("class_type" in v or "type" in v or "nodes" in v):
                maybe = v.get("nodes") or v.get("graph")
                if isinstance(maybe, dict):
                    nodes = maybe
                    break

    if not isinstance(nodes, dict):
        return None

    metadata = _gi_get_comfyui_metadata(nodes)
    if metadata is None:
        return None

    extra_metadata_str = data.get("extraMetadata")
    if extra_metadata_str:
        extra_data = _gi_parse_extrametadata(extra_metadata_str)

        if extra_data:
            if not metadata.get("prompt") and extra_data.get("prompt"):
                metadata["prompt"] = extra_data["prompt"]

            if not metadata.get("negative") and extra_data.get("negative"):
                metadata["negative"] = extra_data["negative"]

            for key in ["steps", "cfg_scale", "sampler", "seed"]:
                if not metadata.get(key) and extra_data.get(key):
                    metadata[key] = extra_data[key]

    if metadata.get("prompt"):
        metadata["prompt"] = _gi_clean_prompt_text(metadata["prompt"])

    if metadata.get("negative"):
        metadata["negative"] = _gi_clean_prompt_text(metadata["negative"])

    if metadata:
        return {"workflow": data, "prompt_data": metadata}
    return None


def _gi_to_a1111_format(normalized):
    """Convert normalized (non-A1111) metadata into an A1111-style parameters dict."""
    out = {"prompt": normalized.get("prompt", ""), "negative": normalized.get("negative", ""), "extra": ""}
    extra_parts = []
    steps = normalized.get("steps") or "0"
    extra_parts.append(f"Steps: {steps}")
    if normalized.get("sampler"):
        extra_parts.append(f"Sampler: {normalized['sampler']}")
    if normalized.get("cfg_scale"):
        extra_parts.append(f"CFG scale: {normalized['cfg_scale']}")
    if normalized.get("seed"):
        extra_parts.append(f"Seed: {normalized['seed']}")
    width = normalized.get("width")
    height = normalized.get("height")
    if width and height:
        extra_parts.append(f"Size: {width}x{height}")
    if normalized.get("modelHash"):
        extra_parts.append(f"Model hash: {normalized['modelHash']}")
    if normalized.get("model"):
        extra_parts.append(f"Model: {normalized['model']}")
    if normalized.get("denoise"):
        extra_parts.append(f"Denoising strength: {normalized['denoise']}")
    out["extra"] = ", ".join(extra_parts)
    return out


def _gi_convert_to_a1111_metadata(raw_metadata, file_type):
    """Run the full format-detection cascade and normalize the result to an A1111-style dict.

    raw_metadata: dict of lowercase-keyed metadata fields (as produced by
    PIL's image.info, supplemented with EXIF/XP comment text for jpg/webp).
    file_type: one of 'png', 'jpg', 'webp' (anything else is treated like png).
    """
    detected_format_name = None
    normalized_metadata = None

    for format_def in METADATA_FORMATS:
        format_name = format_def["name"]

        if file_type == "webp" and format_def.get("webp_fields"):
            fields = format_def["webp_fields"]
        elif file_type == "jpg" and format_def.get("jpg_fields"):
            fields = format_def["jpg_fields"]
        elif format_def.get("png_fields"):
            fields = format_def["png_fields"]
        else:
            continue

        if format_def.get("verify_fields"):
            if not all(field in raw_metadata for field in fields):
                continue

        raw_value = None
        for field in fields:
            if field in raw_metadata:
                raw_value = raw_metadata[field]
                break

        if not raw_value or not isinstance(raw_value, str):
            continue

        if format_def.get("regex"):
            if not format_def["regex"].search(raw_value):
                continue

        parsed_json = None
        if "is_json" in format_def:
            parsed_json = _gi_safe_json_parse(raw_value)

            if parsed_json is None:
                colon_pos = raw_value.find(":")
                if colon_pos != -1:
                    parsed_json = _gi_safe_json_parse(raw_value[colon_pos + 1:])

            if format_def["is_json"] and parsed_json is None:
                continue

            if not format_def["is_json"] and parsed_json is not None:
                continue

        if normalized_metadata:
            continue

        if format_name == "ComfyUI":
            json_data = parsed_json or _gi_try_parse_json_inside_text(raw_value)

            comfy_result = None
            if json_data:
                comfy_result = _gi_detect_comfyui_workflow(json_data)

            if comfy_result and not comfy_result["prompt_data"].get("prompt"):
                if "workflow" in raw_metadata:
                    workflow_data = raw_metadata["workflow"]
                    workflow_json = None

                    if isinstance(workflow_data, str):
                        workflow_json = _gi_safe_json_parse(workflow_data) or _gi_try_parse_json_inside_text(workflow_data)
                    elif isinstance(workflow_data, dict):
                        workflow_json = workflow_data

                    if workflow_json:
                        comfy_result_workflow = _gi_detect_comfyui_workflow(workflow_json)
                        if comfy_result_workflow and comfy_result_workflow["prompt_data"].get("prompt"):
                            comfy_result["prompt_data"]["prompt"] = comfy_result_workflow["prompt_data"].get("prompt", "")
                            comfy_result["prompt_data"]["negative"] = comfy_result_workflow["prompt_data"].get("negative", "")

            elif not comfy_result and "workflow" in raw_metadata:
                workflow_data = raw_metadata["workflow"]

                if isinstance(workflow_data, str):
                    workflow_json = _gi_safe_json_parse(workflow_data) or _gi_try_parse_json_inside_text(workflow_data)
                    if workflow_json:
                        comfy_result = _gi_detect_comfyui_workflow(workflow_json)
                elif isinstance(workflow_data, dict):
                    comfy_result = _gi_detect_comfyui_workflow(workflow_data)

            if comfy_result:
                normalized_metadata = comfy_result["prompt_data"]
                normalized_metadata["_is_comfyui"] = True
                normalized_metadata["_workflow"] = comfy_result["workflow"]
                detected_format_name = "ComfyUI"

        elif format_name == "NovelAI":
            if parsed_json:
                normalized_metadata = _gi_get_novelai_metadata(json.dumps(parsed_json))
            else:
                normalized_metadata = _gi_get_novelai_metadata(raw_value)
            if normalized_metadata:
                detected_format_name = "NovelAI"

        elif format_name == "A1111":
            normalized_metadata = _gi_get_a1111_metadata(raw_value)
            if normalized_metadata and (normalized_metadata.get("prompt") or normalized_metadata.get("extra")):
                detected_format_name = "A1111"

        elif not normalized_metadata:
            normalized_metadata = _gi_get_a1111_metadata(raw_value)
            detected_format_name = format_name

    if not normalized_metadata:
        return None

    if isinstance(normalized_metadata, dict) and "prompt" in normalized_metadata and "extra" in normalized_metadata:
        result = normalized_metadata
    else:
        result = _gi_to_a1111_format(normalized_metadata)

    result["_is_comfyui"] = normalized_metadata.get("_is_comfyui", False)
    result["_workflow"] = normalized_metadata.get("_workflow")
    result["_format_name"] = detected_format_name

    return result


def _gi_read_exif_user_comment_bytes(exif_bytes):
    """Best-effort extraction of the EXIF UserComment string from raw EXIF bytes, via piexif."""
    if not exif_bytes:
        return None
    try:
        exif = piexif.load(exif_bytes)
    except Exception:
        return None
    exif_comment = (exif or {}).get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
    if not exif_comment:
        return None
    try:
        return piexif.helper.UserComment.load(exif_comment)
    except ValueError:
        try:
            return exif_comment.decode("utf8", errors="ignore") or None
        except Exception:
            return None


def _gi_build_raw_metadata(image: Image.Image) -> tuple[dict, str]:
    """Build a lowercase-keyed metadata field dict (as epd.py's per-format
    extractors would produce) from a PIL Image already loaded into memory,
    plus a normalized file_type string ('png' / 'jpg' / 'webp' / other).
    """
    info = (image.info or {}).copy()

    fmt = (image.format or "").upper()
    if fmt in ("JPEG", "JPG", "MPO"):
        file_type = "jpg"
    elif fmt == "WEBP":
        file_type = "webp"
    elif fmt == "PNG":
        file_type = "png"
    else:
        file_type = "png"

    raw_metadata = {}
    for key, value in info.items():
        raw_metadata[str(key).lower()] = value

    # Supplement with the EXIF UserComment, which PIL exposes only as raw
    # bytes under "exif" rather than as a decoded string field.
    if "user_comment" not in raw_metadata and "exif" in info:
        decoded = _gi_read_exif_user_comment_bytes(info["exif"])
        if decoded:
            raw_metadata["user_comment"] = decoded

    return raw_metadata, file_type


def _gi_looks_like_unparsed_json(value) -> bool:
    """True if value is a string that looks like a raw JSON object/array
    rather than human-readable generation-parameters text. Used to detect
    cases like a PNG "comment" field holding NovelAI-style JSON, which the
    legacy GIF-comment branch would otherwise treat as final geninfo text
    instead of letting the multi-format detector parse it properly.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    return (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]"))


def read_info_from_image(image: Image.Image) -> tuple[str | None, dict]:
    """Read generation info from an image, checking standard metadata first, then stealth info if needed."""

    def read_standard():
        items = (image.info or {}).copy()

        geninfo = items.pop("parameters", None)
        geninfo_is_unparsed = _gi_looks_like_unparsed_json(geninfo)

        if "exif" in items:
            exif_data = items["exif"]
            try:
                exif = piexif.load(exif_data)
            except OSError:
                # memory / exif was not valid so piexif tried to read from a file
                exif = None
            exif_comment = (exif or {}).get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
            try:
                exif_comment = piexif.helper.UserComment.load(exif_comment)
            except ValueError:
                exif_comment = exif_comment.decode("utf8", errors="ignore")

            if exif_comment:
                geninfo = exif_comment
                # An EXIF UserComment holding raw JSON (ComfyUI/NovelAI/etc.)
                # is not finished geninfo text; let the extended detector
                # below parse it properly instead of dumping the raw JSON.
                geninfo_is_unparsed = _gi_looks_like_unparsed_json(geninfo)
        elif "comment" in items:  # for gif
            if isinstance(items["comment"], bytes):
                geninfo = items["comment"].decode("utf8", errors="ignore")
            else:
                geninfo = items["comment"]
            # A "comment" field holding raw JSON (e.g. NovelAI-style metadata)
            # is not actually finished geninfo text; flag it so the extended
            # detector below gets a chance to parse it properly instead.
            geninfo_is_unparsed = _gi_looks_like_unparsed_json(geninfo)

        for field in IGNORED_INFO_KEYS:
            items.pop(field, None)

        if items.get("Software", None) == "NovelAI":
            try:
                json_info = json.loads(items["Comment"])
                sampler = sd_samplers.samplers_map.get(json_info["sampler"], "Euler a")

                geninfo = f"""{items["Description"]}
    Negative prompt: {json_info["uc"]}
    Steps: {json_info["steps"]}, Sampler: {sampler}, CFG scale: {json_info["scale"]}, Seed: {json_info["seed"]}, Size: {image.width}x{image.height}, Clip skip: 2, ENSD: 31337"""
                geninfo_is_unparsed = False
            except Exception:
                errors.report("Error parsing NovelAI image generation parameters", exc_info=True)

        return geninfo, items, geninfo_is_unparsed

    geninfo, items, geninfo_is_unparsed = read_standard()

    # If the standard A1111/Forge-native path (PNG "parameters" text chunk,
    # EXIF UserComment, GIF comment, or NovelAI Software/Comment combo)
    # didn't yield anything usable, fall back to the broader multi-format
    # detector ported from epd.py: ComfyUI, NovelAI (JSON-only variants),
    # SwarmUI, Fooocus, InvokeAI, DrawThings, Midjourney, DAVANT,
    # CivitAI-tagged parameters, and generic A1111-style text it didn't
    # already catch.
    if not geninfo or geninfo_is_unparsed:
        try:
            raw_metadata, file_type = _gi_build_raw_metadata(image)
            normalized = _gi_convert_to_a1111_metadata(raw_metadata, file_type)
        except Exception:
            normalized = None
            errors.report("Error running extended generation-metadata detection", exc_info=True)

        if normalized:
            prompt = normalized.get("prompt", "")
            negative = normalized.get("negative", "")
            extra = normalized.get("extra", "")

            parts = []
            if prompt:
                parts.append(prompt)
            if negative:
                parts.append(f"{NEGATIVE_PREFIX}{negative}")
            if extra:
                parts.append(extra)

            if parts:
                geninfo = "\n".join(parts)
        # else: detector found nothing better; keep whatever geninfo was
        # already determined (including raw unparsed text), matching prior
        # fall-back behavior of never discarding a non-empty value.

    return geninfo, items



def image_data(data):
    import gradio as gr

    try:
        image = read(io.BytesIO(data))
        textinfo, _ = read_info_from_image(image)
        return textinfo, None
    except Exception:
        pass

    try:
        text = data.decode("utf8")
        assert len(text) < 10000
        return text, None

    except Exception:
        pass

    return gr.skip(), None


def flatten(img, bgcolor):
    """replaces transparency with bgcolor (example: "#ffffff"), returning an RGB mode image with no transparency"""

    if img.mode == "RGBA":
        background = Image.new("RGBA", img.size, bgcolor)
        background.paste(img, mask=img)
        img = background

    return img.convert("RGB")


def read(fp, **kwargs):
    image = Image.open(fp, **kwargs)
    image = fix_image(image)

    return image


def fix_image(image: Image.Image):
    if image is None:
        return None

    try:
        image = ImageOps.exif_transpose(image)
        image = fix_png_transparency(image)
    except Exception:
        pass

    return image


def fix_png_transparency(image: Image.Image):
    if image.mode not in ("RGB", "P") or not isinstance(image.info.get("transparency"), bytes):
        return image

    image = image.convert("RGBA")
    return image


def save_video(p, frames: list[np.ndarray], fps: int = 16, *, basename: str = "", info: str = None, audio_copy: os.PathLike = None) -> str:
    height, width, channels = frames[0].shape
    assert channels == 3, "Frames must be in (H, W, 3) RGB format"

    folder = opts.outdir_samples or opts.outdir_videos
    extension = opts.video_container
    os.makedirs(folder, exist_ok=True)

    if isinstance(p, str):
        add_number = True
        basename = p
        file_decoration = ""
    else:
        namegen = FilenameGenerator(p, p.seeds[0], p.prompts[0], image=None, basename=basename)
        file_decoration = opts.samples_filename_pattern or ("[seed]" if opts.save_to_dirs else "[seed]-[prompt_spaces]")
        file_decoration = namegen.apply(file_decoration)
        add_number = opts.save_images_add_number or file_decoration == ""
        if file_decoration != "" and add_number:
            file_decoration = f"-{file_decoration}"

    if add_number:
        basecount = get_next_sequence_number(folder, basename)
        fullfn = None
        for i in range(256):
            fn = f"{basecount + i:05}" if basename == "" else f"{basename}-{basecount + i:04}"
            fullfn = os.path.join(folder, f"{fn}{file_decoration}.{extension}")
            if not os.path.exists(fullfn):
                break
    else:
        fullfn = os.path.join(folder, f"{file_decoration}.{extension}")

    crf = int(opts.video_crf)
    preset = str(opts.video_preset)
    profile = str(opts.video_profile)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "auto",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]

    if audio_copy is not None:
        cmd += [
            "-i",
            audio_copy,
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-acodec",
            "copy",
        ]

    cmd += [
        "-vcodec",
        "h264",
        "-crf",
        str(crf),
        "-preset",
        str(preset),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        profile,
        "-metadata",
        f"description={str(info)}",
        fullfn,
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()

    if opts.save_txt and info is not None:
        txt_fullfn = os.path.join(folder, f"{file_decoration}.txt")
        with open(txt_fullfn, "w", encoding="utf8") as file:
            file.write(f"{info}\n")

    return fullfn
