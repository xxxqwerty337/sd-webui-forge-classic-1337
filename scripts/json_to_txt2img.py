import os
import re
import json
import glob
from datetime import datetime
from modules import scripts, shared, sd_samplers
from modules.processing import StableDiffusionProcessingTxt2Img, process_images, Processed
from modules import images as images_module

# ---------------------------------------------------------------------------
# Hires. fix preset applied when the "Enable Hires. fix" checkbox is on.
# Edit the values here to change the preset without touching the logic below.
#
# Per-generation attributes set on the processing object:
HIRES_FIX_SETTINGS = {
    "denoising_strength": 0.3,                      # Denoising strength
    "hr_scale": 1.25,                               # Hires upscale (ratio)
    "hr_upscaler": "realesrganX4plusAnime_v1",      # Hires upscaler
    "hr_second_pass_steps": 20,                     # Hires steps
    "hr_cfg": 4.0,                                  # Hires CFG Scale (Neo)
    "hr_shift": 3,                                  # Hires Shift (Neo)
    "hr_additional_modules": ["Use same choices"],  # Hires Module 1 (Neo)
}
# Global options toggled around the run (saved and restored afterwards):
HIRES_GLOBAL_OPTS = {
    "randn_source": "GPU",    # RNG
    "emphasis": "Original",   # Emphasis
}

MODE_NORMAL = "Normal (run all JSONs)"
MODE_RERUN = "Rerun flagged images"
# ---------------------------------------------------------------------------


class Script(scripts.Script):
    def title(self):
        return "Generate from Consolidated JSON Metadata"

    def show(self, is_img2img):
        return not is_img2img

    # ------------------------------------------------------------------ #
    # Path helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _base_path():
        return shared.data_path if getattr(shared, "data_path", None) else os.getcwd()

    @staticmethod
    def _output_base():
        return os.path.join(Script._base_path(), "output", "txt2img-images")

    @staticmethod
    def _json_folder():
        return os.path.join(Script._base_path(), "scripts", "json_to_txt2img")

    @staticmethod
    def _scan_output_folders():
        """Return a list of 'date/stem' relative labels for every generation
        subfolder under output/txt2img-images/. Newest dates first."""
        base = Script._output_base()
        labels = []
        if not os.path.isdir(base):
            return labels
        for date_name in sorted(os.listdir(base), reverse=True):
            date_path = os.path.join(base, date_name)
            if not os.path.isdir(date_path):
                continue
            for stem in sorted(os.listdir(date_path)):
                if os.path.isdir(os.path.join(date_path, stem)):
                    labels.append(f"{date_name}/{stem}")
        return labels

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def ui(self, is_img2img):
        import gradio as gr

        mode = gr.Radio(
            label="Mode",
            choices=[MODE_NORMAL, MODE_RERUN],
            value=MODE_NORMAL,
        )

        # -------- Normal-mode controls (unchanged behaviour) -------- #
        with gr.Group() as normal_group:
            use_random_seed = gr.Checkbox(label='Use random seed (ignore seed from JSON)', value=False)
            batch_count = gr.Number(label='Batch count (how many times to run through entire JSON)', value=1, minimum=1, maximum=100, step=1, precision=0)
            run_adetailer = gr.Checkbox(label='Run ADetailer', value=True)
            use_hires_fix = gr.Checkbox(label='Enable Hires. fix (upscaler realesrganX4plusAnime_v1, 20 steps, CFG 4)', value=False)
            hires_denoise = gr.Number(label='Hires denoising strength', value=HIRES_FIX_SETTINGS["denoising_strength"], minimum=0.0, maximum=1.0, step=0.01)
            hires_upscale = gr.Number(label='Hires upscale multiplier', value=HIRES_FIX_SETTINGS["hr_scale"], minimum=1.0, maximum=4.0, step=0.05)

        # -------- Rerun-mode controls (new) -------- #
        with gr.Group(visible=False) as rerun_group:
            gr.Markdown(
                "**Rerun mode.** Pick a generation folder, load its images, click the "
                "bad ones to flag them, then Generate. Flagged entries are rerun from the "
                "*same* source JSON with a **random seed**, `batch count` times each. New "
                "images are saved back into the same folder (originals are left in place)."
            )
            with gr.Row():
                rerun_folder = gr.Dropdown(
                    label="Output folder to review (date / json-name)",
                    choices=self._scan_output_folders(),
                    value=None,
                )
                refresh_btn = gr.Button("↻ Refresh list", scale=0)
            load_btn = gr.Button("Load images from folder")
            gallery = gr.Gallery(
                label="Click an image to preview it, then use the flag button below",
                columns=6, height=520, allow_preview=True,
            )
            selected_label = gr.Markdown("**Selected:** (none)")
            flag_btn = gr.Button("⚑ Flag / unflag selected image")
            flagged = gr.Textbox(
                label="Flagged filenames (toggled by the button above; you can also edit this by hand)",
                value="", lines=3,
            )
            flagged_count = gr.Markdown("**Flagged: 0**")
            clear_btn = gr.Button("Clear flags")
            rerun_batch = gr.Number(label='Rerun batch count (attempts per flagged image)', value=4, minimum=1, maximum=100, step=1, precision=0)
            rerun_adetailer = gr.Checkbox(label='Run ADetailer on reruns', value=True)
            rerun_hires = gr.Checkbox(label='Enable Hires. fix on reruns', value=False)

        # Hidden carriers that must reach run():
        loaded_folder_path = gr.Textbox(visible=False, value="")   # absolute path of loaded folder
        gallery_files = gr.State([])                               # absolute file paths in gallery order
        current_index = gr.State(-1)                               # index of the previewed/selected image

        # ---- event wiring ----
        def _toggle_mode(m):
            is_rerun = (m == MODE_RERUN)
            return gr.update(visible=not is_rerun), gr.update(visible=is_rerun)
        mode.change(_toggle_mode, inputs=[mode], outputs=[normal_group, rerun_group])

        def _refresh():
            return gr.update(choices=Script._scan_output_folders())
        refresh_btn.click(_refresh, outputs=[rerun_folder])

        def _load(folder_label):
            if not folder_label:
                return [], "", "", "**Flagged: 0**", [], -1, "**Selected:** (none)"
            abs_path = os.path.join(Script._output_base(), *folder_label.split("/"))
            files = sorted(glob.glob(os.path.join(abs_path, "*.png")))
            return (files, abs_path, "", f"**Loaded {len(files)} images — Flagged: 0**",
                    files, -1, "**Selected:** (none)")
        load_btn.click(
            _load,
            inputs=[rerun_folder],
            outputs=[gallery, loaded_folder_path, flagged, flagged_count,
                     gallery_files, current_index, selected_label],
        )

        def _on_select(files, evt: gr.SelectData):
            idx = evt.index if evt.index is not None else -1
            if 0 <= idx < len(files):
                return idx, f"**Selected:** {os.path.basename(files[idx])}"
            return -1, "**Selected:** (none)"
        gallery.select(_on_select, inputs=[gallery_files], outputs=[current_index, selected_label])

        def _toggle_flag(files, cur, flagged_text):
            count = len([x for x in re.split(r"[\n,]+", flagged_text or "") if x.strip()])
            if cur is None or cur < 0 or cur >= len(files):
                return flagged_text, f"**Flagged: {count}** — click an image first"
            name = os.path.basename(files[cur])
            current = [x.strip() for x in re.split(r"[\n,]+", flagged_text or "") if x.strip()]
            if name in current:
                current.remove(name)
                state = "unflagged"
            else:
                current.append(name)
                state = "flagged"
            return "\n".join(current), f"**Flagged: {len(current)}** — {name} {state}"
        flag_btn.click(_toggle_flag, inputs=[gallery_files, current_index, flagged], outputs=[flagged, flagged_count])

        def _clear():
            return "", "**Flagged: 0**"
        clear_btn.click(_clear, outputs=[flagged, flagged_count])

        # Order here MUST match the run() signature below.
        return [
            mode,
            use_random_seed, batch_count, run_adetailer, use_hires_fix, hires_denoise, hires_upscale,
            rerun_folder, loaded_folder_path, flagged, rerun_batch, rerun_adetailer, rerun_hires,
        ]

    # ------------------------------------------------------------------ #
    # Shared setup helpers
    # ------------------------------------------------------------------ #
    def _get_adetailer(self):
        """Locate the ADetailer always-on script and build its args, or (None, None)."""
        adetailer_script = None
        adetailer_args = None
        try:
            for script in scripts.scripts_txt2img.alwayson_scripts:
                if script.title().lower() == "adetailer":
                    adetailer_script = script
                    print(f"✓ ADetailer extension found: {script.title()}")
                    adetailer_args = [
                        True, False,
                        {
                            "ad_model": "face_yolov8m.pt", "ad_model_classes": "", "ad_tab_enable": True,
                            "ad_prompt": "", "ad_negative_prompt": "", "ad_confidence": 0.3,
                            "ad_mask_filter_method": "Area", "ad_mask_k": 0,
                            "ad_mask_min_ratio": 0.0, "ad_mask_max_ratio": 1.0,
                            "ad_dilate_erode": 4, "ad_x_offset": 0, "ad_y_offset": 0,
                            "ad_mask_merge_invert": "None", "ad_mask_blur": 40,
                            "ad_denoising_strength": 0.2, "ad_inpaint_only_masked": True,
                            "ad_inpaint_only_masked_padding": 52,
                            "ad_use_inpaint_width_height": True,
                            "ad_inpaint_width": 1024, "ad_inpaint_height": 1024,
                            "ad_use_steps": False, "ad_steps": 28,
                            "ad_use_cfg_scale": True, "ad_cfg_scale": 3.0,
                            "ad_use_checkpoint": False, "ad_checkpoint": None,
                            "ad_use_vae": False, "ad_vae": None,
                            "ad_use_sampler": True, "ad_sampler": "Euler a",
                            "ad_scheduler": "Automatic", "ad_use_noise_multiplier": False,
                            "ad_noise_multiplier": 1.0, "ad_use_clip_skip": False,
                            "ad_clip_skip": 1, "ad_restore_face": False,
                            "ad_controlnet_model": "None", "ad_controlnet_module": "None",
                            "ad_controlnet_weight": 1.0,
                            "ad_controlnet_guidance_start": 0.0,
                            "ad_controlnet_guidance_end": 1.0,
                        }
                    ]
                    break
            if adetailer_script is None:
                print("⚠ Warning: ADetailer extension not found in alwayson_scripts.")
        except Exception as e:
            print(f"⚠ Warning: Error checking for ADetailer: {str(e)}")
        return adetailer_script, adetailer_args

    def _apply_hires_globals(self):
        original = {}
        for opt_name, opt_value in HIRES_GLOBAL_OPTS.items():
            if hasattr(shared.opts, opt_name):
                original[opt_name] = getattr(shared.opts, opt_name)
                setattr(shared.opts, opt_name, opt_value)
                print(f"✓ Set global option {opt_name} = {opt_value}")
            else:
                print(f"⚠ Global option '{opt_name}' not found on this build; skipping")
        return original

    def _restore_globals(self, original):
        for opt_name, opt_value in original.items():
            setattr(shared.opts, opt_name, opt_value)
            print(f"✓ Restored global option {opt_name} = {opt_value}")

    # ------------------------------------------------------------------ #
    # Core per-entry generation (shared by normal + rerun)
    # ------------------------------------------------------------------ #
    def _generate_and_save(self, image_key, metadata, idx, batch_num, batch_count,
                           outdir, use_random_seed, run_adetailer, use_hires_fix,
                           hires_denoise, hires_upscale, adetailer_script, adetailer_args):
        if batch_count > 1:
            filename_pattern = f"[seed]-{idx}-b{batch_num}"
        else:
            filename_pattern = f"[seed]-{idx}"

        shared.opts.samples_filename_pattern = filename_pattern
        print(f"DEBUG: Filename pattern: {filename_pattern}")

        proc = StableDiffusionProcessingTxt2Img(
            sd_model=shared.sd_model, prompt="", negative_prompt="",
            steps=20, cfg_scale=7.5, width=512, height=512,
            sampler_name="Euler a", seed=-1,
            outpath_samples=outdir,
            do_not_save_samples=True, do_not_save_grid=True
        )

        proc.scripts = scripts.scripts_txt2img
        proc.script_args = [None] * len(proc.scripts.scripts)

        if 'prompt' in metadata:
            proc.prompt = metadata['prompt']
        if 'negative' in metadata:
            proc.negative_prompt = metadata['negative']
        elif 'negative_prompt' in metadata:
            proc.negative_prompt = metadata['negative_prompt']
        if 'steps' in metadata:
            proc.steps = int(metadata['steps'])
        if 'cfg_scale' in metadata:
            proc.cfg_scale = float(metadata['cfg_scale'])

        if not use_random_seed and 'seed' in metadata:
            proc.seed = int(metadata['seed'])
            print(f"DEBUG: Using JSON seed: {proc.seed}")
        else:
            proc.seed = -1
            print(f"DEBUG: Using random seed")

        if 'sampler' in metadata:
            sampler_name = metadata['sampler']
            sampler_match = next((s for s in sd_samplers.all_samplers if s.name.lower() == sampler_name.lower()), None)
            if sampler_match:
                proc.sampler_name = sampler_match.name
            else:
                proc.sampler_name = "Euler a"
                print(f"Warning: Sampler '{sampler_name}' not found, using Euler a")
        if 'width' in metadata:
            proc.width = int(metadata['width'])
        if 'height' in metadata:
            proc.height = int(metadata['height'])

        if use_hires_fix:
            proc.enable_hr = True
            proc.denoising_strength = hires_denoise
            proc.hr_scale = hires_upscale
            proc.hr_resize_x = 0
            proc.hr_resize_y = 0
            proc.hr_upscaler = HIRES_FIX_SETTINGS["hr_upscaler"]
            proc.hr_second_pass_steps = HIRES_FIX_SETTINGS["hr_second_pass_steps"]
            for attr in ("hr_cfg", "hr_shift", "hr_additional_modules"):
                setattr(proc, attr, HIRES_FIX_SETTINGS[attr])
            if not hasattr(proc, 'extra_generation_params'):
                proc.extra_generation_params = {}
            proc.extra_generation_params.update({
                "Denoising strength": hires_denoise,
                "Hires upscale": hires_upscale,
                "Hires upscaler": HIRES_FIX_SETTINGS["hr_upscaler"],
                "Hires steps": HIRES_FIX_SETTINGS["hr_second_pass_steps"],
                "Hires CFG Scale": HIRES_FIX_SETTINGS["hr_cfg"],
                "Hires Shift": HIRES_FIX_SETTINGS["hr_shift"],
            })
            print(f"→ Hires. fix ON: {HIRES_FIX_SETTINGS['hr_upscaler']} "
                  f"x{hires_upscale}, denoise {hires_denoise}, "
                  f"{HIRES_FIX_SETTINGS['hr_second_pass_steps']} steps, "
                  f"CFG {HIRES_FIX_SETTINGS['hr_cfg']}")

        print(f"Generating: {proc.prompt[:100]}...")
        result = process_images(proc)
        print(f"Generated {len(result.images)} image(s)")

        if adetailer_script and adetailer_args and run_adetailer:
            try:
                print("→ Running ADetailer...")
                from modules.scripts import PostprocessImageArgs

                if hasattr(proc, '_ad_disabled'):
                    delattr(proc, '_ad_disabled')

                ad_params = adetailer_args[2]
                if not hasattr(proc, 'extra_generation_params'):
                    proc.extra_generation_params = {}

                proc.extra_generation_params.update({
                    "ADetailer model": ad_params.get("ad_model", "face_yolov8m.pt"),
                    "ADetailer confidence": ad_params.get("ad_confidence", 0.7),
                    "ADetailer dilate erode": ad_params.get("ad_dilate_erode", 4),
                    "ADetailer mask blur": ad_params.get("ad_mask_blur", 40),
                    "ADetailer denoising strength": ad_params.get("ad_denoising_strength", 0.2),
                    "ADetailer inpaint only masked": ad_params.get("ad_inpaint_only_masked", True),
                    "ADetailer inpaint padding": ad_params.get("ad_inpaint_only_masked_padding", 52),
                    "ADetailer use inpaint width height": ad_params.get("ad_use_inpaint_width_height", True),
                    "ADetailer inpaint width": ad_params.get("ad_inpaint_width", 1024),
                    "ADetailer inpaint height": ad_params.get("ad_inpaint_height", 1024),
                    "ADetailer use separate CFG scale": ad_params.get("ad_use_cfg_scale", True),
                    "ADetailer CFG scale": ad_params.get("ad_cfg_scale", 3.0),
                })

                for i, img in enumerate(result.images):
                    try:
                        pp = PostprocessImageArgs(img, index=i)
                    except TypeError:
                        pp = PostprocessImageArgs(img)
                    original_img = img.copy()
                    adetailer_script.postprocess_image(proc, pp, *adetailer_args)
                    if pp.image != original_img:
                        print(f"  ✓ Image {i+1} modified by ADetailer")
                        result.images[i] = pp.image

                from modules.processing import create_infotext
                result.info = create_infotext(proc, proc.all_prompts, proc.all_seeds, proc.all_subseeds, None, 0, 0)
                print("✓ ADetailer complete")

            except Exception as e:
                print(f"⚠ ADetailer failed: {str(e)}")

        actual_seed = result.all_seeds[0] if result.all_seeds else proc.seed
        has_image_name = 'image_name' in metadata and metadata['image_name']

        if has_image_name:
            original_name = os.path.splitext(metadata['image_name'])[0]
            if batch_count > 1:
                final_filename = f"{original_name}-{actual_seed}-b{batch_num}"
            else:
                final_filename = f"{original_name}-{actual_seed}"

            print(f"DEBUG: Using image_name: {metadata['image_name']}")
            print(f"DEBUG: Final filename: {final_filename}.png")

            from PIL import PngImagePlugin
            for i, image in enumerate(result.images):
                full_path = os.path.join(outdir, f"{final_filename}.png")
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text("parameters", result.info)
                image.save(full_path, pnginfo=pnginfo)
                print(f"Saved directly: {final_filename}.png (no seq prefix)")
        else:
            # No image_name: name it {idx:05d}-{seed}[-b{batch}]-{idx}. The zero-padded
            # index leads so files sort in entry order; the trailing raw index is the
            # token the reverse-matcher reads. Saved directly (no A1111 seq prefix).
            if batch_count > 1:
                final_filename = f"{idx:05d}-{actual_seed}-b{batch_num}-{idx}"
            else:
                final_filename = f"{idx:05d}-{actual_seed}-{idx}"

            print(f"DEBUG: No image_name field")
            print(f"DEBUG: Actual seed used: {actual_seed}")
            print(f"DEBUG: Direct filename: {final_filename}.png")

            from PIL import PngImagePlugin
            for i, image in enumerate(result.images):
                full_path = os.path.join(outdir, f"{final_filename}.png")
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text("parameters", result.info)
                image.save(full_path, pnginfo=pnginfo)
                print(f"Saved directly: {final_filename}.png (no seq prefix)")

        shared.state.sampling_step = 0
        return result

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def run(self, p, mode, use_random_seed, batch_count, run_adetailer, use_hires_fix,
            hires_denoise, hires_upscale, rerun_folder, loaded_folder_path, flagged,
            rerun_batch, rerun_adetailer, rerun_hires, *args):
        import warnings
        import logging
        logging.getLogger('ControlNet').setLevel(logging.ERROR)
        warnings.filterwarnings('ignore', message='.*Sampler Scheduler autocorrection.*')

        if mode == MODE_RERUN:
            return self._run_rerun(
                loaded_folder_path=loaded_folder_path,
                flagged=flagged,
                rerun_batch=int(rerun_batch),
                rerun_adetailer=rerun_adetailer,
                rerun_hires=rerun_hires,
            )
        return self._run_normal(
            use_random_seed=use_random_seed,
            batch_count=int(batch_count),
            run_adetailer=run_adetailer,
            use_hires_fix=use_hires_fix,
            hires_denoise=float(hires_denoise),
            hires_upscale=float(hires_upscale),
        )

    # ------------------------------------------------------------------ #
    # Normal mode (unchanged behaviour, now routed through _generate_and_save)
    # ------------------------------------------------------------------ #
    def _run_normal(self, use_random_seed, batch_count, run_adetailer, use_hires_fix,
                    hires_denoise, hires_upscale):
        print(f"Settings: Use random seed = {use_random_seed}, Batch count = {batch_count}, Run ADetailer = {run_adetailer}, Hires fix = {use_hires_fix}")
        if use_hires_fix:
            print(f"          Hires denoise = {hires_denoise}, Hires upscale = {hires_upscale}")

        base_path = self._base_path()
        print(f"Base path: {base_path}")

        json_folder = self._json_folder()
        print(f"Looking for JSON files in: {json_folder}")

        if not os.path.exists(json_folder):
            print(f"Error: Folder '{json_folder}' not found")
            return

        json_files = glob.glob(os.path.join(json_folder, "*.json"))
        if not json_files:
            print(f"Error: No JSON files found in '{json_folder}'")
            return

        print(f"Found {len(json_files)} JSON file(s) to process:")
        for jf in json_files:
            print(f"  - {os.path.basename(jf)}")

        adetailer_script, adetailer_args = self._get_adetailer()

        original_global_opts = {}
        if use_hires_fix:
            original_global_opts = self._apply_hires_globals()

        date_folder = datetime.now().strftime("%Y-%m-%d")
        total_images_all_files = 0
        result = None

        for json_file in json_files:
            print(f"\n{'='*60}")
            print(f"Processing: {os.path.basename(json_file)}")
            print(f"{'='*60}")

            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)
                    print(f"Loaded {len(file_data)} entries")
            except Exception as e:
                print(f"Error reading JSON file '{json_file}': {str(e)}")
                continue

            json_basename = os.path.basename(json_file)
            original_save_to_dirs = shared.opts.save_to_dirs
            original_samples_filename_pattern = shared.opts.samples_filename_pattern
            shared.opts.save_to_dirs = False

            default_base_outdir = os.path.join(base_path, "output", "txt2img-images")

            if json_basename == "prompt_data.json":
                outdir = os.path.join(default_base_outdir, date_folder)
            elif json_basename.startswith("prompt_data_"):
                suffix = json_basename.replace("prompt_data_", "").replace(".json", "")
                outdir = os.path.join(default_base_outdir, date_folder, suffix)
            else:
                suffix = os.path.splitext(json_basename)[0]
                outdir = os.path.join(default_base_outdir, date_folder, suffix)

            print(f"Output directory: {outdir}")
            os.makedirs(outdir, exist_ok=True)

            valid_entries = [entry for entry in file_data.items() if entry[1] and ('prompt' in entry[1] or 'parameters' in entry[1])]

            original_count = len(valid_entries)
            valid_entries = [(k, v) for k, v in valid_entries if not v.get('skip', False)]
            skipped_count = original_count - len(valid_entries)
            if skipped_count > 0:
                print(f"Skipping {skipped_count} entries marked with skip=true")

            total_images = len(valid_entries) * batch_count
            print(f"Total images to generate: {len(valid_entries)} entries × {batch_count} batch(es) = {total_images}")

            if len(valid_entries) == 0:
                print("No valid entries found, skipping...")
                shared.opts.save_to_dirs = original_save_to_dirs
                shared.opts.samples_filename_pattern = original_samples_filename_pattern
                continue

            total_images_all_files += total_images
            shared.state.job_count = total_images
            shared.state.job_no = 0

            for batch_num in range(batch_count):
                if batch_count > 1:
                    print(f"\n{'='*60}")
                    print(f"Starting Batch {batch_num + 1}/{batch_count}")
                    print(f"{'='*60}")

                for idx, (image_key, metadata) in enumerate(valid_entries):
                    overall_idx = batch_num * len(valid_entries) + idx
                    shared.state.job_no = overall_idx
                    shared.state.textinfo = f"[{json_basename}] Batch {batch_num + 1}/{batch_count} - Job {idx + 1}/{len(valid_entries)}: {image_key}"
                    print(f"\nBatch {batch_num + 1}/{batch_count} - Job {idx + 1}/{len(valid_entries)} for {image_key}")

                    result = self._generate_and_save(
                        image_key, metadata, idx, batch_num, batch_count, outdir,
                        use_random_seed, run_adetailer, use_hires_fix,
                        hires_denoise, hires_upscale, adetailer_script, adetailer_args,
                    )

                    shared.state.job_no = overall_idx + 1

                print(f"\nBatch {batch_num + 1}/{batch_count} complete")

            shared.opts.save_to_dirs = original_save_to_dirs
            shared.opts.samples_filename_pattern = original_samples_filename_pattern

        self._restore_globals(original_global_opts)

        shared.state.textinfo = f"Complete: {total_images_all_files} total images"
        print(f"\n{'='*60}")
        print(f"ALL DONE - {total_images_all_files} images generated!")
        print(f"{'='*60}")
        return result

    # ------------------------------------------------------------------ #
    # Rerun mode (new)
    # ------------------------------------------------------------------ #
    def _find_source_json(self, stem):
        """Given an output-folder stem, find the source JSON whose computed output
        suffix matches it (inverse of the normal-mode naming rule)."""
        json_folder = self._json_folder()
        for jf in glob.glob(os.path.join(json_folder, "*.json")):
            b = os.path.basename(jf)
            if b == "prompt_data.json":
                suffix = ""          # goes to the date root, no stem subfolder
            elif b.startswith("prompt_data_"):
                suffix = b[len("prompt_data_"):-len(".json")]
            else:
                suffix = os.path.splitext(b)[0]
            if suffix == stem:
                return jf
        return None

    def _index_for_file(self, fname, valid_entries):
        """Reverse an output filename to its 0-based index in valid_entries.

        Naming schemes that may appear in one output folder:
          * with image_name : {image_name}-{seed}[-b{n}].png
          * current         : {idx:05d}-{seed}[-b{n}]-{idx}.png  (3 nums, idx LAST)
          * legacy          : {seq}-{seed}-{idx}.png             (3 nums, idx LAST)
          * transitional    : {idx}-{seed}[-b{n}].png            (2 nums, idx FIRST)
        The batch marker -b{n} may sit anywhere and is stripped first.
        """
        base = os.path.splitext(os.path.basename(fname))[0]
        base = re.sub(r"-b\d+", "", base)           # drop -b<n> wherever it sits

        # 1) image_name scheme: strip trailing -<seed>, match image_name stems.
        core = re.sub(r"-\d+$", "", base)
        for i, (key, meta) in enumerate(valid_entries):
            iname = meta.get('image_name')
            if iname and os.path.splitext(iname)[0] == core:
                return i

        # 2) positional schemes, disambiguated by numeric-token count.
        parts = base.split("-")
        if parts and all(p.isdigit() for p in parts):
            if len(parts) >= 3:          # {padidx}-{seed}-{idx} / {seq}-{seed}-{idx} -> idx LAST
                idx = int(parts[-1])
            elif len(parts) == 2:        # {idx}-{seed}                               -> idx FIRST
                idx = int(parts[0])
            else:
                return None
            if 0 <= idx < len(valid_entries):
                return idx

        return None

    def _run_rerun(self, loaded_folder_path, flagged, rerun_batch,
                   rerun_adetailer, rerun_hires):
        print(f"\n{'='*60}")
        print("RERUN MODE")
        print(f"{'='*60}")

        if not loaded_folder_path or not os.path.isdir(loaded_folder_path):
            print("Error: No valid folder loaded. Pick a folder and press 'Load images' first.")
            return

        stem = os.path.basename(os.path.normpath(loaded_folder_path))
        print(f"Loaded folder: {loaded_folder_path}")
        print(f"Folder stem  : {stem}")

        source_json = self._find_source_json(stem)
        if source_json is None:
            print(f"Error: Could not find a source JSON in {self._json_folder()} "
                  f"whose output folder is '{stem}'.")
            return
        print(f"Source JSON  : {os.path.basename(source_json)}")

        try:
            with open(source_json, 'r', encoding='utf-8') as f:
                file_data = json.load(f)
        except Exception as e:
            print(f"Error reading source JSON '{source_json}': {str(e)}")
            return

        # Rebuild the EXACT list the original run numbered against, so positional
        # (idx) filenames reverse correctly: prompt/parameters present, skip=true
        # removed, original JSON order preserved.
        valid_entries = [(k, v) for k, v in file_data.items()
                         if v and ('prompt' in v or 'parameters' in v)]
        valid_entries = [(k, v) for k, v in valid_entries if not v.get('skip', False)]

        flagged_names = [x.strip() for x in re.split(r"[\n,]+", flagged or "") if x.strip()]
        if not flagged_names:
            print("Error: No images were flagged. Click images in the gallery first.")
            return
        print(f"Flagged files: {len(flagged_names)}")

        # Reverse each flagged filename to its index in valid_entries (dedup, keep order).
        seen = set()
        filtered = []  # list of (orig_idx, key, meta)
        for fn in flagged_names:
            oi = self._index_for_file(fn, valid_entries)
            if oi is None:
                print(f"⚠ Could not match flagged file to a JSON entry: {fn}")
                continue
            if oi in seen:
                continue
            seen.add(oi)
            key, meta = valid_entries[oi]
            filtered.append((oi, key, meta))
            print(f"  {fn}  ->  entry #{oi}: {key}")

        if not filtered:
            print("Error: None of the flagged files could be matched to JSON entries.")
            return

        print(f"Matched {len(filtered)} unique entries to rerun, "
              f"{rerun_batch} attempt(s) each = {len(filtered) * rerun_batch} images.")

        adetailer_script, adetailer_args = self._get_adetailer()

        original_global_opts = {}
        if rerun_hires:
            original_global_opts = self._apply_hires_globals()

        # Reruns land back in the same folder the user reviewed.
        outdir = loaded_folder_path
        original_save_to_dirs = shared.opts.save_to_dirs
        original_samples_filename_pattern = shared.opts.samples_filename_pattern
        shared.opts.save_to_dirs = False

        total = len(filtered) * rerun_batch
        shared.state.job_count = total
        shared.state.job_no = 0
        result = None

        # Hires numbers on reruns just use the preset defaults.
        hires_denoise = HIRES_FIX_SETTINGS["denoising_strength"]
        hires_upscale = HIRES_FIX_SETTINGS["hr_scale"]

        for batch_num in range(rerun_batch):
            if rerun_batch > 1:
                print(f"\n{'='*60}")
                print(f"Rerun batch {batch_num + 1}/{rerun_batch}")
                print(f"{'='*60}")

            for pos, (orig_idx, image_key, metadata) in enumerate(filtered):
                overall_idx = batch_num * len(filtered) + pos
                shared.state.job_no = overall_idx
                shared.state.textinfo = f"[RERUN {stem}] Batch {batch_num + 1}/{rerun_batch} - {image_key}"
                print(f"\nRerun {batch_num + 1}/{rerun_batch} - {pos + 1}/{len(filtered)} (entry #{orig_idx}) for {image_key}")

                result = self._generate_and_save(
                    image_key, metadata, orig_idx, batch_num, rerun_batch, outdir,
                    use_random_seed=True,            # reruns always use a fresh seed
                    run_adetailer=rerun_adetailer,
                    use_hires_fix=rerun_hires,
                    hires_denoise=hires_denoise,
                    hires_upscale=hires_upscale,
                    adetailer_script=adetailer_script,
                    adetailer_args=adetailer_args,
                )

                shared.state.job_no = overall_idx + 1

        shared.opts.save_to_dirs = original_save_to_dirs
        shared.opts.samples_filename_pattern = original_samples_filename_pattern
        self._restore_globals(original_global_opts)

        shared.state.textinfo = f"Rerun complete: {total} images"
        print(f"\n{'='*60}")
        print(f"RERUN DONE - {total} images generated into {outdir}")
        print(f"{'='*60}")
        return result


if __name__ == "__main__":
    print("Run within AUTOMATIC1111 WebUI")