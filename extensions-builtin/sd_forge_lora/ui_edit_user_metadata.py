import datetime
import html
import json
import os
import random
import re

import gradio as gr

from modules import ui_extra_networks_user_metadata


# Key in the LoRA's .json that holds per-preview activation texts.
# Value is a dict: {"<preview file name>": "<activation text>"}, e.g.
#   {"my_lora.jpg": "standing pose", "my_lora.preview.2.jpg": "sitting, umbrella"}
# Texts are tied to the image file name, so they follow the image when previews
# are reordered or renumbered after a delete.
PREVIEW_TEXTS_KEY = "preview activation text"

# Must match the extensions used by UserMetadataEditor.get_all_preview_paths / renumber_previews_after_deletion
PREVIEW_EXTENSIONS = ["png", "jpg", "jpeg", "webp"]

MINI_TOOLBAR_CSS = """
<style>
.mini-preview-nav, .mini-preview-actions {
    gap: 4px !important;
    flex-wrap: nowrap !important;
    align-items: center !important;
    margin-top: 4px !important;
}
.mini-preview-nav > *, .mini-preview-actions > * {
    min-width: 0 !important;
}
.mini-icon-btn {
    min-width: 0 !important;
    flex: 1 1 0 !important;
    padding: 2px 0 !important;
    font-size: 1rem !important;
    line-height: 1.6 !important;
}
.mini-preview-counter {
    flex: 2 1 0 !important;
    min-width: 0 !important;
    text-align: center;
    font-size: 0.85rem;
    opacity: 0.8;
    white-space: nowrap;
}
.mini-toolbar-style {
    display: none !important;
}
</style>
"""


def is_non_comma_tagset(tags: dict[str, int]) -> bool:
    average_tag_length = sum(len(x) for x in tags.keys()) / len(tags)
    return average_tag_length >= 16


re_word = re.compile(r"[-_\w']+")
re_comma = re.compile(r" *, *")


def build_tags(metadata: dict) -> list[tuple[str, int]]:
    tags = {}

    ss_tag_frequency: dict[str, dict[str, int]] = metadata.get("ss_tag_frequency", {})
    if ss_tag_frequency is not None and hasattr(ss_tag_frequency, "items"):
        for _, tags_dict in ss_tag_frequency.items():
            for tag, tag_count in tags_dict.items():
                tag = tag.strip()
                tags[tag] = tags.get(tag, 0) + int(tag_count)

    if tags and is_non_comma_tagset(tags):
        new_tags = {}

        for text, text_count in tags.items():
            for word in re.findall(re_word, text):
                if len(word) < 3:
                    continue

                new_tags[word] = new_tags.get(word, 0) + text_count

        tags = new_tags

    ordered_tags = sorted(tags.keys(), key=tags.get, reverse=True)

    return [(tag, tags[tag]) for tag in ordered_tags]


class LoraUserMetadataEditor(ui_extra_networks_user_metadata.UserMetadataEditor):
    def __init__(self, ui, tabname, page):
        super().__init__(ui, tabname, page)

        self.select_sd_version: gr.Dropdown = None

        self.taginfo: gr.HighlightedText = None
        self.edit_activation_text: gr.Textbox = None
        self.slider_preferred_weight: gr.Slider = None
        self.edit_notes: gr.Textbox = None
        self.checkbox_pinned: gr.Checkbox = None

        # Preview management components
        self.preview_gallery_html = None
        self.preview_index_state = None
        self.preview_counter = None
        self.button_prev_preview = None
        self.button_next_preview = None
        self.button_delete_preview = None
        self.button_add_preview = None

        # Mini toolbar under the preview image (mirrors the bottom buttons)
        self.preview_counter_mini = None
        self.button_prev_preview_mini = None
        self.button_next_preview_mini = None
        self.button_cancel_mini = None
        self.button_fetch_civitai_mini = None
        self.button_add_preview_mini = None
        self.button_delete_preview_mini = None
        self.button_save_mini = None

        # Per-preview activation text
        self.edit_preview_activation_text = None
        self.preview_texts_json = None  # hidden textbox holding unsaved per-preview texts as JSON

        # Reorder grid components
        self.reorder_grid_html = None
        self.reorder_new_order_state = None
        self.button_save_reorder = None
        self.reorder_status = None

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save_lora_user_metadata(self, name, desc, sd_version, activation_text, preferred_weight, negative_text, notes, pinned, preview_index=0, preview_text="", preview_texts_json="{}"):
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["sd version"] = sd_version
        user_metadata["activation text"] = activation_text
        user_metadata["preferred weight"] = preferred_weight
        user_metadata["negative text"] = negative_text
        user_metadata["notes"] = notes
        user_metadata["pinned"] = pinned

        # Per-preview activation texts: include whatever is currently typed in the box,
        # and keep only texts for preview files that still exist.
        texts = self._commit_text(name, preview_index, preview_text, self._parse_texts(preview_texts_json))
        existing = {os.path.basename(path) for path, _ in self.get_all_preview_paths(name)}
        texts = {key: value for key, value in texts.items() if key in existing and value.strip()}

        if texts:
            user_metadata[PREVIEW_TEXTS_KEY] = texts
        else:
            user_metadata.pop(PREVIEW_TEXTS_KEY, None)

        self.write_user_metadata(name, user_metadata)

    # ------------------------------------------------------------------
    # Per-preview activation text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_texts(texts_json):
        """Parse the hidden JSON textbox into a {file name: text} dict."""
        if isinstance(texts_json, dict):
            data = texts_json
        else:
            try:
                data = json.loads(texts_json) if texts_json else {}
            except (TypeError, ValueError):
                return {}

        if not isinstance(data, dict):
            return {}

        return {str(key): str(value) for key, value in data.items() if value is not None}

    @staticmethod
    def _dump_texts(texts):
        return json.dumps(texts, ensure_ascii=False)

    def _preview_key(self, name, index):
        """File name of the preview at `index` in the editor's list, or None."""
        previews = self.get_all_preview_paths(name)
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None

        if 0 <= index < len(previews):
            return os.path.basename(previews[index][0])

        return None

    def _commit_text(self, name, index, text, texts):
        """Store the text typed for the preview at `index` into the texts dict."""
        key = self._preview_key(name, index)
        if key is None:
            return texts

        text = (text or "").strip()
        if text:
            texts[key] = text
        else:
            texts.pop(key, None)

        return texts

    def commit_preview_text(self, name, index, text, texts_json):
        texts = self._commit_text(name, index, text, self._parse_texts(texts_json))
        return self._dump_texts(texts)

    def load_saved_preview_texts(self, name):
        saved = self.get_user_metadata(name).get(PREVIEW_TEXTS_KEY) or {}
        if not isinstance(saved, dict):
            return {}

        return {str(key): str(value) for key, value in saved.items() if value}

    def _base_path(self, name):
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        if not filename:
            return None

        return os.path.splitext(filename)[0]

    def _numbered_preview_paths(self, name):
        """Same scan as renumber_previews_after_deletion: every name.preview.N.ext for N in 1..99."""
        basename = self._base_path(name)
        if not basename:
            return []

        found = []
        for index in range(1, 100):
            for ext in PREVIEW_EXTENSIONS:
                path = f"{basename}.preview.{index}.{ext}"
                if os.path.exists(path):
                    found.append(path)
                    break

        return found

    @staticmethod
    def _remap_texts(texts, rename_map, keep, removed=()):
        """
        Move texts to their preview's new file name.

        texts:      {old file name: text}
        rename_map: {old file name: new file name}, for files that were renamed
        keep:       file names that existed before the change (anything else is stale and dropped)
        removed:    file names that were deleted
        """
        result = {}
        for key, value in texts.items():
            if key not in keep or key in removed:
                continue

            result[rename_map.get(key, key)] = value

        return result

    def _remap_saved_texts(self, name, rename_map, keep, removed=()):
        """
        Keep the texts already saved in the .json attached to the right images after a
        delete or reorder. The image files are renamed straight away, so the saved keys
        have to follow them straight away too. Unsaved edits are not written here.
        """
        user_metadata = self.get_user_metadata(name)
        saved = user_metadata.get(PREVIEW_TEXTS_KEY)
        if not isinstance(saved, dict) or not saved:
            return

        remapped = self._remap_texts(saved, rename_map, keep, removed)
        if remapped == saved:
            return

        if remapped:
            user_metadata[PREVIEW_TEXTS_KEY] = remapped
        else:
            user_metadata.pop(PREVIEW_TEXTS_KEY, None)

        self.write_user_metadata(name, user_metadata)

    # ------------------------------------------------------------------
    # Showing a preview in the modal
    # ------------------------------------------------------------------

    def show_preview(self, name, index, texts_json):
        """
        Show the preview at `index` and load its activation text.
        Returns values for:
            html_preview, preview_index_state, preview_counter, preview_counter_mini,
            button_delete_preview, button_delete_preview_mini, edit_preview_activation_text
        """
        texts = self._parse_texts(texts_json)
        previews = self.get_all_preview_paths(name)

        if not previews:
            return (
                "<div class='preview-empty'>No previews</div>",
                0,
                "No previews",
                "0 / 0",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(value="", label="Activation text for this preview (add a preview first)", interactive=False),
            )

        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        index = max(0, min(index, len(previews) - 1))

        preview_path, _ = previews[index]
        preview_url = self.page.link_preview(preview_path)

        preview_html = f"""
        <div class='card standalone-card-preview'>
            <img src="{html.escape(preview_url)}" class="preview">
        </div>
        """

        key = os.path.basename(preview_path)
        total = len(previews)

        return (
            preview_html,
            index,
            f"Preview {index + 1} of {total}",
            f"{index + 1} / {total}",
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(value=texts.get(key, ""), label=f"Activation text for preview {index + 1} of {total}", interactive=True),
        )

    def open_preview_editor(self, name):
        """Called when the modal opens: load saved texts and show the first preview."""
        texts_json = self._dump_texts(self.load_saved_preview_texts(name))
        return (*self.show_preview(name, 0, texts_json), texts_json)

    def navigate_preview_with_text(self, name, index, direction, text, texts_json):
        """Remember the current preview's text, then move to the previous/next preview."""
        texts_json = self.commit_preview_text(name, index, text, texts_json)

        count = len(self.get_all_preview_paths(name))
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0

        new_index = (index + int(direction)) % count if count else 0

        return (*self.show_preview(name, new_index, texts_json), texts_json)

    # ------------------------------------------------------------------
    # Original preview helpers (kept for compatibility)
    # ------------------------------------------------------------------

    def get_metadata_table(self, name):
        table = super().get_metadata_table(name)
        item = self.page.items.get(name, {})
        metadata = item.get("metadata") or {}

        keys = {
            "ss_output_name": "Output name:",
            "ss_sd_model_name": "Model:",
            "ss_clip_skip": "Clip skip:",
            "ss_network_module": "Kohya module:",
        }

        for key, label in keys.items():
            value = metadata.get(key, None)
            if value is not None and str(value) != "None":
                table.append((label, html.escape(value)))

        ss_training_started_at = metadata.get("ss_training_started_at")
        if ss_training_started_at:
            table.append(("Date trained:", datetime.datetime.fromtimestamp(float(ss_training_started_at), datetime.UTC).strftime("%Y-%m-%d %H:%M")))

        ss_bucket_info = metadata.get("ss_bucket_info")
        if ss_bucket_info and "buckets" in ss_bucket_info:
            resolutions = {}
            for _, bucket in ss_bucket_info["buckets"].items():
                resolution = bucket["resolution"]
                resolution = f"{resolution[1]}x{resolution[0]}"

                resolutions[resolution] = resolutions.get(resolution, 0) + int(bucket["count"])

            resolutions_list = sorted(resolutions.keys(), key=resolutions.get, reverse=True)
            resolutions_text = html.escape(", ".join(resolutions_list[0:4]))
            if len(resolutions) > 4:
                resolutions_text += ", ..."
                resolutions_text = f"<span title='{html.escape(', '.join(resolutions_list))}'>{resolutions_text}</span>"

            table.append(("Resolutions:" if len(resolutions_list) > 1 else "Resolution:", resolutions_text))

        image_count = 0
        for _, params in metadata.get("ss_dataset_dirs", {}).items():
            image_count += int(params.get("img_count", 0))

        if image_count:
            table.append(("Dataset size:", image_count))

        return table

    def put_values_into_components(self, name):
        user_metadata = self.get_user_metadata(name)
        values = super().put_values_into_components(name)

        item = self.page.items.get(name, {})
        metadata = item.get("metadata") or {}

        tags = build_tags(metadata)
        gradio_tags = [(tag, str(count)) for tag, count in tags[0:24]]

        # Get preview count for initial display
        preview_count = self.get_preview_count(name)
        counter_text = f"Preview 1 of {preview_count}" if preview_count > 0 else "No previews"
        delete_visible = gr.update(visible=(preview_count > 0))

        # Get reorder grid HTML
        reorder_grid = self.get_reorder_grid_html(name)

        return [
            *values[0:5],
            item.get("sd_version", "Unknown"),
            user_metadata.get("pinned", False),
            gr.update(value=gradio_tags, visible=True if tags else False),
            user_metadata.get("activation text", ""),
            float(user_metadata.get("preferred weight", 0.0)),
            user_metadata.get("negative text", ""),
            gr.update(visible=True if tags else False),
            gr.update(value=self.generate_random_prompt_from_tags(tags), visible=True if tags else False),
            counter_text,
            delete_visible,
            reorder_grid,
        ]

    def generate_random_prompt(self, name):
        item = self.page.items.get(name, {})
        metadata = item.get("metadata") or {}
        tags = build_tags(metadata)

        return self.generate_random_prompt_from_tags(tags)

    def generate_random_prompt_from_tags(self, tags):
        max_count = None
        res = []
        for tag, count in tags:
            if not max_count:
                max_count = count

            v = random.random() * max_count
            if count > v:
                for x in "({[]})":
                    tag = tag.replace(x, "\\" + x)
                res.append(tag)

        return ", ".join(sorted(res))

    def get_preview_count(self, name):
        """Get the number of previews for this LORA"""
        previews = self.get_all_preview_paths(name)
        return len(previews)

    def navigate_preview(self, name, current_index, direction):
        """
        Navigate through previews in the modal (without activation text handling).
        Returns: (new_preview_html, new_index, counter_text, delete_button_visible)
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return "<div class='preview-empty'>No previews</div>", 0, "No previews", gr.update(visible=False)

        current_index = int(current_index)
        new_index = current_index + direction

        if new_index < 0:
            new_index = len(previews) - 1
        elif new_index >= len(previews):
            new_index = 0

        preview_path, _ = previews[new_index]
        preview_url = self.page.link_preview(preview_path)

        preview_html = f"""
        <div class='card standalone-card-preview'>
            <img src="{html.escape(preview_url)}" class="preview">
        </div>
        """

        counter_text = f"Preview {new_index + 1} of {len(previews)}"
        delete_visible = gr.update(visible=True)

        return preview_html, new_index, counter_text, delete_visible

    def update_preview_display(self, name):
        """
        Update preview-related components when opening the editor.
        Returns: (preview_html, index, counter, delete_visible)
        """
        return self.navigate_preview(name, 0, 0)

    # ------------------------------------------------------------------
    # Delete / reorder / add, keeping texts attached to their images
    # ------------------------------------------------------------------

    def delete_preview_with_text(self, name, index, text, texts_json):
        """
        Delete the preview at `index`. The remaining numbered previews get renumbered by
        the base class, so the texts are moved to the new file names.
        Returns: (status, texts_json)
        """
        texts = self._commit_text(name, index, text, self._parse_texts(texts_json))

        previews = self.get_all_preview_paths(name)
        numbered = self._numbered_preview_paths(name)
        keep = {os.path.basename(path) for path, _ in previews} | {os.path.basename(path) for path in numbered}
        rename_map = {}
        removed = set()

        try:
            idx = int(index)
        except (TypeError, ValueError):
            idx = -1

        basename = self._base_path(name)
        if basename and 0 <= idx < len(previews):
            deleted_path = previews[idx][0]
            removed.add(os.path.basename(deleted_path))

            # Mirror renumber_previews_after_deletion: remaining numbered previews become 1, 2, 3...
            remaining = [path for path in numbered if path != deleted_path]
            for new_number, old_path in enumerate(remaining, start=1):
                new_path = f"{basename}.preview.{new_number}{os.path.splitext(old_path)[1]}"
                if new_path != old_path:
                    rename_map[os.path.basename(old_path)] = os.path.basename(new_path)

        _, status = self.delete_preview_by_index(name, index)

        if removed and str(status).startswith("Deleted"):
            texts = self._remap_texts(texts, rename_map, keep, removed)
            self._remap_saved_texts(name, rename_map, keep, removed)

        return status, self._dump_texts(texts)

    def handle_reorder_and_refresh(self, name, new_order_json, texts_json="{}"):
        """
        Handle reorder, refresh grid, update preview display, and move the
        per-preview texts to the images' new file names.
        Returns: (status_html, grid_html, preview_html, texts_json)
        """
        texts = self._parse_texts(texts_json)

        # Work out where each file will end up, the same way _execute_preview_reorder does
        previews_before = self.get_all_preview_paths(name)
        keep = {os.path.basename(path) for path, _ in previews_before}
        rename_map = {}
        basename = self._base_path(name)
        try:
            new_order = json.loads(new_order_json)
            if basename and isinstance(new_order, list) and sorted(new_order) == list(range(len(previews_before))):
                for new_pos, old_pos in enumerate(new_order):
                    old_path = previews_before[old_pos][0]
                    ext = os.path.splitext(old_path)[1]
                    new_path = f"{basename}{ext}" if new_pos == 0 else f"{basename}.preview.{new_pos}{ext}"
                    rename_map[os.path.basename(old_path)] = os.path.basename(new_path)
        except (TypeError, ValueError):
            rename_map = {}

        # Perform reorder
        success_msg, error_msg = self.reorder_previews(name, new_order_json)

        if success_msg and not error_msg and success_msg != "Order unchanged" and rename_map:
            texts = self._remap_texts(texts, rename_map, keep)
            self._remap_saved_texts(name, rename_map, keep)

        # Format status message
        if error_msg:
            status_html = f"<div class='reorder-status error'>❌ {error_msg}</div>"
        elif success_msg:
            status_html = f"<div class='reorder-status success'>✅ {success_msg}</div>"
        else:
            status_html = "<div class='reorder-status error'>❌ Unknown error occurred</div>"

        # Refresh grid HTML
        grid_html = self.get_reorder_grid_html(name)

        # Get updated preview HTML (first preview after reorder)
        preview_html = self.get_card_html(name)

        return status_html, grid_html, preview_html, self._dump_texts(texts)

    def add_preview_with_text(self, gallery_index, gallery, name):
        """
        Add a preview from the generated gallery and jump to it so its text can be typed straight away.
        Returns: (status, index_of_new_preview)
        """
        before = {path for path, _ in self.get_all_preview_paths(name)}

        result = self.add_preview_from_gallery(gallery_index, gallery, name)
        status = result[1] if isinstance(result, (tuple, list)) and len(result) == 2 else ""

        after = self.get_all_preview_paths(name)
        new_index = next((i for i, (path, _) in enumerate(after) if path not in before), None)

        if new_index is None:
            return status, 0

        return status or "Added preview", new_index

    def format_reorder_status(self, success_msg, error_msg):
        """
        Format status messages for reorder operations.
        Returns: HTML string
        """
        if error_msg:
            return f"<div class='reorder-status error'>❌ {error_msg}</div>"
        elif success_msg:
            return f"<div class='reorder-status success'>✅ {success_msg}</div>"
        else:
            return ""

    # ------------------------------------------------------------------
    # CivitAI
    # ------------------------------------------------------------------

    def create_extra_default_items_in_left_column(self):
        import network

        self.select_sd_version = gr.Dropdown(choices=network.SD_VERSION, value="Unknown", label="Preset", interactive=True)

    def fetch_from_civitai(self, name):
        """
        Override parent method to return LoRA-specific outputs.
        """
        print(f"[DEBUG LoRA] fetch_from_civitai called with name: {name}")

        # Call parent method to get base data
        parent_result = super().fetch_from_civitai(name)

        # Parent returns (description, notes, status) - 3 values
        # We need to return (description, sd_version, activation_text, preferred_weight, negative_text, notes, status) - 7 values

        if len(parent_result) == 3:
            # Unpack parent results
            description, notes, status = parent_result

            # If status indicates error, return error for all fields
            if "❌" in status or isinstance(description, dict):  # gr.update() is a dict
                print(f"[DEBUG LoRA] Parent returned error, propagating")
                return (
                    description,  # May be gr.update()
                    gr.update(),  # sd_version unchanged
                    gr.update(),  # activation_text unchanged
                    gr.update(),  # preferred_weight unchanged
                    gr.update(),  # negative_text unchanged
                    notes,  # May be gr.update()
                    status
                )

            # Success case - extract additional info from notes
            print(f"[DEBUG LoRA] Parent successful, extracting LoRA-specific data")

            # Try to extract trained words from notes
            activation_text = ""
            sd_version = "Unknown"

            if "Trained Words:" in notes:
                match = re.search(r'Trained Words: (.+?)(?:\n|$)', notes)
                if match:
                    activation_text = match.group(1)
                    print(f"[DEBUG LoRA] Extracted activation text: {activation_text}")

            if "Base Model:" in notes:
                match = re.search(r'Base Model: (.+?)(?:\n|$)', notes)
                if match:
                    base_model = match.group(1)
                    # Map to SD version
                    sd_version_map = {
                        'SD 1.5': 'SD1', 'SD 1.4': 'SD1', 'SD 1': 'SD1',
                        'SDXL 1.0': 'SDXL', 'SDXL 0.9': 'SDXL', 'SDXL Turbo': 'SDXL',
                        'SDXL Lightning': 'SDXL', 'Pony': 'SDXL',
                        'Flux.1 D': 'Flux', 'Flux.1 S': 'Flux', 'Flux.1': 'Flux',
                    }
                    sd_version = sd_version_map.get(base_model, 'Unknown')
                    print(f"[DEBUG LoRA] Extracted SD version: {sd_version}")

            return (
                description,
                sd_version,
                activation_text,
                0.0,  # preferred_weight unchanged
                "",   # negative_text unchanged
                notes,
                status
            )
        else:
            print(f"[DEBUG LoRA] Unexpected parent return format: {len(parent_result)} values")
            # Fallback - return all unchanged
            return (
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                "<div style='color: red;'>Unexpected error</div>"
            )

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def create_default_editor_elems(self):
        """Same layout as the base class, plus the mini toolbar under the preview image."""
        with gr.Row():
            with gr.Column(scale=2):
                self.edit_name = gr.HTML(elem_classes="extra-network-name")
                self.edit_description = gr.Textbox(label="Description", lines=2, max_lines=4)
                self.html_filedata = gr.HTML()

                self.create_extra_default_items_in_left_column()

            with gr.Column(scale=1, min_width=0):
                self.html_preview = gr.HTML()

                with gr.Row(elem_classes="mini-preview-nav"):
                    self.button_prev_preview_mini = gr.Button("◀", size="sm", elem_classes=["mini-icon-btn", "mini-btn-prev"])
                    self.preview_counter_mini = gr.HTML("", elem_classes="mini-preview-counter")
                    self.button_next_preview_mini = gr.Button("▶", size="sm", elem_classes=["mini-icon-btn", "mini-btn-next"])

                with gr.Row(elem_classes="mini-preview-actions"):
                    self.button_cancel_mini = gr.Button("✕", size="sm", elem_classes=["mini-icon-btn", "mini-btn-cancel"])
                    self.button_fetch_civitai_mini = gr.Button("🌐", size="sm", elem_classes=["mini-icon-btn", "mini-btn-fetch"])
                    self.button_add_preview_mini = gr.Button("➕", size="sm", elem_classes=["mini-icon-btn", "mini-btn-add"])
                    self.button_delete_preview_mini = gr.Button("🗑️", size="sm", variant="stop", visible=False, elem_classes=["mini-icon-btn", "mini-btn-delete"])
                    self.button_save_mini = gr.Button("💾", size="sm", variant="primary", elem_classes=["mini-icon-btn", "mini-btn-save"])

    def create_preview_management_buttons(self):
        """
        Create buttons for preview management (replaces default buttons).
        """
        with gr.Row(elem_classes="preview-navigation-controls"):
            self.button_prev_preview = gr.Button("◀ Previous", size="sm", scale=1)
            self.preview_counter = gr.Markdown("Preview 1 of 1", elem_classes="preview-counter")
            self.button_next_preview = gr.Button("Next ►", size="sm", scale=1)

        with gr.Row(elem_classes="edit-user-metadata-buttons"):
            self.button_cancel = gr.Button('Cancel')
            self.button_fetch_civitai = gr.Button('🌐 Fetch from CivitAI', variant='secondary')
            self.button_add_preview = gr.Button('Add from Generated', variant='primary')
            self.button_delete_preview = gr.Button('Delete This Preview', variant='stop', visible=False)
            self.button_save = gr.Button('Save', variant='primary')

        self.html_status = gr.HTML(elem_classes="edit-user-metadata-status")

        # Hidden state to track current preview index
        self.preview_index_state = gr.State(value=0)

        self.button_cancel.click(fn=None, _js="closePopup")
        self.button_cancel_mini.click(fn=None, _js="closePopup")

    def _refresh_card_js(self):
        return "function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}"

    def create_editor(self):
        gr.HTML(MINI_TOOLBAR_CSS, elem_classes="mini-toolbar-style")

        self.create_default_editor_elems()

        # Add reorder grid (collapsible section)
        with gr.Accordion("🔄 Reorder Preview Images", open=False):
            self.reorder_grid_html = gr.HTML(
                value="<div class='reorder-grid-empty'>Click Edit to load reorder grid</div>",
                elem_classes="preview-reorder-section"
            )

            with gr.Row(elem_classes="reorder-controls"):
                self.button_save_reorder = gr.Button(
                    "💾 Save New Order",
                    variant="primary",
                    size="lg"
                )

            self.reorder_status = gr.HTML(
                value="",
                elem_classes="reorder-status"
            )

            # Hidden state to store new order from JavaScript
            self.reorder_new_order_state = gr.Textbox(
                value="[]",
                visible=False,
                elem_id=f"{self.id_part}_reorder_state",
                elem_classes="reorder-state-input"
            )

        self.taginfo = gr.HighlightedText(label="Training dataset tags")
        self.edit_preview_activation_text = gr.Textbox(
            label="Activation text for this preview",
            info="Used instead of the activation text below when this preview is showing on the card. Leave empty to use the default.",
            lines=2,
            max_lines=8,
        )
        self.edit_activation_text = gr.Text(label="Activation text", info="Will be added to prompt along with Lora")
        self.slider_preferred_weight = gr.Slider(label="Preferred weight", info="Set to 0 to disable", minimum=0.0, maximum=2.0, step=0.01)
        self.edit_negative_text = gr.Text(label="Negative prompt", info="Will be added to negative prompts")
        with gr.Row() as row_random_prompt:
            random_prompt = gr.Textbox(label="Random Prompt", lines=4, max_lines=4, interactive=False, scale=9)
            generate_random_prompt = gr.Button("Generate", size="lg", scale=1)

        self.edit_notes = gr.TextArea(label="Notes", lines=4)
        self.checkbox_pinned = gr.Checkbox(label="Pinned", value=False)

        # Unsaved per-preview texts, as JSON {file name: text}
        self.preview_texts_json = gr.Textbox(value="{}", visible=False, elem_id=f"{self.id_part}_preview_texts")

        generate_random_prompt.click(fn=self.generate_random_prompt, inputs=[self.edit_name_input], outputs=[random_prompt], show_progress=False)

        def select_tag(activation_text, evt: gr.SelectData):
            tag = evt.value[0]

            words = re.split(re_comma, activation_text)
            if tag in words:
                words = [x for x in words if x != tag and x.strip()]
                return ", ".join(words)

            return (activation_text + ", " + tag) if activation_text else tag

        self.taginfo.select(fn=select_tag, inputs=[self.edit_activation_text], outputs=[self.edit_activation_text], show_progress=False)

        # Use custom preview management buttons instead of default
        self.create_preview_management_buttons()

        viewed_components = [
            self.edit_name,
            self.edit_description,
            self.html_filedata,
            self.html_preview,
            self.edit_notes,
            self.select_sd_version,
            self.checkbox_pinned,
            self.taginfo,
            self.edit_activation_text,
            self.slider_preferred_weight,
            self.edit_negative_text,
            row_random_prompt,
            random_prompt,
            self.preview_counter,
            self.button_delete_preview,
            self.reorder_grid_html,
        ]

        # Everything that changes when a different preview is shown
        show_outputs = [
            self.html_preview,
            self.preview_index_state,
            self.preview_counter,
            self.preview_counter_mini,
            self.button_delete_preview,
            self.button_delete_preview_mini,
            self.edit_preview_activation_text,
        ]

        # Remember what's typed in the per-preview box before anything changes the shown preview
        commit_kwargs = dict(
            fn=self.commit_preview_text,
            inputs=[self.edit_name_input, self.preview_index_state, self.edit_preview_activation_text, self.preview_texts_json],
            outputs=[self.preview_texts_json],
            show_progress=False,
        )

        def show_first_preview(name, texts_json):
            return self.show_preview(name, 0, texts_json)

        # When edit button clicked, load components, then previews and their texts
        self.button_edit.click(
            fn=self.put_values_into_components,
            inputs=[self.edit_name_input],
            outputs=viewed_components
        ).then(
            fn=self.open_preview_editor,
            inputs=[self.edit_name_input],
            outputs=[*show_outputs, self.preview_texts_json]
        ).then(
            fn=lambda: gr.update(visible=True),
            inputs=[],
            outputs=[self.box]
        )

        # Previous / next (bottom row and mini toolbar)
        for button, direction in (
            (self.button_prev_preview, -1),
            (self.button_prev_preview_mini, -1),
            (self.button_next_preview, 1),
            (self.button_next_preview_mini, 1),
        ):
            button.click(
                fn=self.navigate_preview_with_text,
                inputs=[self.edit_name_input, self.preview_index_state, gr.State(direction), self.edit_preview_activation_text, self.preview_texts_json],
                outputs=[*show_outputs, self.preview_texts_json],
                show_progress=False
            )

        # Delete preview (bottom row and mini toolbar)
        for button in (self.button_delete_preview, self.button_delete_preview_mini):
            button.click(
                fn=self.delete_preview_with_text,
                inputs=[self.edit_name_input, self.preview_index_state, self.edit_preview_activation_text, self.preview_texts_json],
                outputs=[self.html_status, self.preview_texts_json],
                show_progress=False
            ).then(
                fn=self.show_preview,
                inputs=[self.edit_name_input, self.preview_index_state, self.preview_texts_json],
                outputs=show_outputs
            ).then(
                fn=None,
                _js=self._refresh_card_js(),
                inputs=[self.edit_name_input],
                outputs=[]
            )

        # Fetch from CivitAI (bottom row and mini toolbar)
        for button in (self.button_fetch_civitai, self.button_fetch_civitai_mini):
            button.click(**commit_kwargs).then(
                fn=self.fetch_from_civitai,
                inputs=[self.edit_name_input],
                outputs=[
                    self.edit_description,
                    self.select_sd_version,
                    self.edit_activation_text,
                    self.slider_preferred_weight,
                    self.edit_negative_text,
                    self.edit_notes,
                    self.html_status
                ],
                show_progress=True
            ).then(
                # Refresh preview display after downloading images
                fn=show_first_preview,
                inputs=[self.edit_name_input, self.preview_texts_json],
                outputs=show_outputs
            ).then(
                # Refresh card in main view
                fn=None,
                _js=self._refresh_card_js(),
                inputs=[self.edit_name_input],
                outputs=[]
            )

        # Save reorder button
        self.button_save_reorder.click(**commit_kwargs).then(
            fn=self.handle_reorder_and_refresh,
            _js="function(name, state, texts) { var order = captureAndReturnReorderState(); console.log('JS captured order:', order); return [name, order, texts]; }",
            inputs=[self.edit_name_input, self.reorder_new_order_state, self.preview_texts_json],
            outputs=[self.reorder_status, self.reorder_grid_html, self.html_preview, self.preview_texts_json],
            show_progress=True
        ).then(
            fn=show_first_preview,
            inputs=[self.edit_name_input, self.preview_texts_json],
            outputs=show_outputs
        ).then(
            fn=None,
            _js=self._refresh_card_js(),
            inputs=[self.edit_name_input],
            outputs=[]
        )

        edited_components = [
            self.edit_description,
            self.select_sd_version,
            self.edit_activation_text,
            self.slider_preferred_weight,
            self.edit_negative_text,
            self.edit_notes,
            self.checkbox_pinned,
            self.preview_index_state,
            self.edit_preview_activation_text,
            self.preview_texts_json,
        ]

        # Save (bottom row and mini toolbar)
        self.setup_save_handler(self.button_save, self.save_lora_user_metadata, edited_components)
        self.setup_save_handler(self.button_save_mini, self.save_lora_user_metadata, edited_components)

    def setup_ui(self, gallery):
        """
        Override parent setup_ui to add gallery-based preview management.
        """
        for button in (self.button_add_preview, self.button_add_preview_mini):
            button.click(
                fn=self.commit_preview_text,
                inputs=[self.edit_name_input, self.preview_index_state, self.edit_preview_activation_text, self.preview_texts_json],
                outputs=[self.preview_texts_json],
                show_progress=False
            ).then(
                fn=self.add_preview_with_text,
                _js=f"function(x, y, z){{return [selected_gallery_index_id('{self.tabname + '_gallery_container'}'), y, z]}}",
                inputs=[self.edit_name_input, gallery, self.edit_name_input],
                outputs=[self.html_status, self.preview_index_state],
                show_progress=False
            ).then(
                fn=self.show_preview,
                inputs=[self.edit_name_input, self.preview_index_state, self.preview_texts_json],
                outputs=[
                    self.html_preview,
                    self.preview_index_state,
                    self.preview_counter,
                    self.preview_counter_mini,
                    self.button_delete_preview,
                    self.button_delete_preview_mini,
                    self.edit_preview_activation_text,
                ]
            ).then(
                fn=None,
                _js=self._refresh_card_js(),
                inputs=[self.edit_name_input],
                outputs=[]
            )