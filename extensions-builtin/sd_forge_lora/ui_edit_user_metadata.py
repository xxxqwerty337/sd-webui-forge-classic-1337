import datetime
import html
import json
import random
import re

import gradio as gr

from modules import ui_extra_networks_user_metadata


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
        self.checkbox_pinned: gr.Checkbox = None  # ADD THIS LINE
        
        # Preview management components
        self.preview_gallery_html = None
        self.preview_index_state = None
        self.preview_counter = None
        self.button_prev_preview = None
        self.button_next_preview = None
        self.button_delete_preview = None
        self.button_add_preview = None
        
        
        # Reorder grid components
        self.reorder_grid_html = None
        self.reorder_new_order_state = None
        self.button_save_reorder = None
        self.reorder_status = None

    def save_lora_user_metadata(self, name, desc, sd_version, activation_text, preferred_weight, negative_text, notes, pinned):
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["sd version"] = sd_version
        user_metadata["activation text"] = activation_text
        user_metadata["preferred weight"] = preferred_weight
        user_metadata["negative text"] = negative_text
        user_metadata["notes"] = notes
        user_metadata["pinned"] = pinned

        self.write_user_metadata(name, user_metadata)

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
            user_metadata.get("pinned", False),  # NEW: pinned checkbox value
            gr.update(value=gradio_tags, visible=True if tags else False),
            user_metadata.get("activation text", ""),
            float(user_metadata.get("preferred weight", 0.0)),
            user_metadata.get("negative text", ""),
            gr.update(visible=True if tags else False),
            gr.update(value=self.generate_random_prompt_from_tags(tags), visible=True if tags else False),
            counter_text,
            delete_visible,
            reorder_grid,  # NEW: reorder grid HTML
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
        Navigate through previews in the modal.
        Returns: (new_preview_html, new_index, counter_text, delete_button_visible)
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return "<div class='preview-empty'>No previews</div>", 0, "No previews", gr.update(visible=False)
        
        # Calculate new index
        current_index = int(current_index)
        new_index = current_index + direction
        
        # Wrap around
        if new_index < 0:
            new_index = len(previews) - 1
        elif new_index >= len(previews):
            new_index = 0
        
        # Get preview at new index
        preview_path, _ = previews[new_index]
        preview_url = self.page.link_preview(preview_path)
        
        # Create HTML for single large preview
        preview_html = f"""
        <div class='card standalone-card-preview'>
            <img src="{html.escape(preview_url)}" class="preview">
        </div>
        """
        
        counter_text = f"Preview {new_index + 1} of {len(previews)}"
        delete_visible = gr.update(visible=True)  # Always allow deletion
        
        return preview_html, new_index, counter_text, delete_visible
    
    def update_preview_display(self, name):
        """
        Update all preview-related components when opening the editor.
        Returns: (preview_html, index, counter, delete_visible)
        """
        return self.navigate_preview(name, 0, 0)

    def handle_reorder_and_refresh(self, name, new_order_json):
        """
        Handle reorder, refresh grid, and update preview display.
        Returns: (status_html, grid_html, preview_html)
        """
        
        
        # Perform reorder
        success_msg, error_msg = self.reorder_previews(name, new_order_json)
        
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
        
        return status_html, grid_html, preview_html

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
                import re
                match = re.search(r'Trained Words: (.+?)(?:\n|$)', notes)
                if match:
                    activation_text = match.group(1)
                    print(f"[DEBUG LoRA] Extracted activation text: {activation_text}")
            
            if "Base Model:" in notes:
                import re
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

    def create_editor(self):
        self.create_default_editor_elems()
        
        # Add reorder grid (collapsible section)
        with gr.Accordion("🔄 Reorder Preview Images", open=False) as reorder_accordion:
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
        self.edit_activation_text = gr.Text(label="Activation text", info="Will be added to prompt along with Lora")
        self.slider_preferred_weight = gr.Slider(label="Preferred weight", info="Set to 0 to disable", minimum=0.0, maximum=2.0, step=0.01)
        self.edit_negative_text = gr.Text(label="Negative prompt", info="Will be added to negative prompts")
        with gr.Row() as row_random_prompt:
            with gr.Column(scale=8):
                random_prompt = gr.Textbox(label="Random prompt", lines=4, max_lines=4, interactive=False)

            with gr.Column(scale=1, min_width=120):
                generate_random_prompt = gr.Button("Generate", size="lg", scale=1)

        self.edit_notes = gr.TextArea(label="Notes", lines=4)
        self.checkbox_pinned = gr.Checkbox(label="Pinned", value=False)  # ADD THIS LINE

        generate_random_prompt.click(fn=self.generate_random_prompt, inputs=[self.edit_name_input], outputs=[random_prompt], show_progress=False)

        def select_tag(activation_text, evt: gr.SelectData):
            tag = evt.value[0]

            words = re.split(re_comma, activation_text)
            if tag in words:
                words = [x for x in words if x != tag and x.strip()]
                return ", ".join(words)

            return activation_text + ", " + tag if activation_text else tag

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
            self.checkbox_pinned,  # NEW: pinned checkbox
            self.taginfo,
            self.edit_activation_text,
            self.slider_preferred_weight,
            self.edit_negative_text,
            row_random_prompt,
            random_prompt,
            self.preview_counter,
            self.button_delete_preview,
            self.reorder_grid_html,  # NEW: reorder grid
        ]

        # When edit button clicked, load components AND update preview display
        self.button_edit.click(
            fn=self.put_values_into_components, 
            inputs=[self.edit_name_input], 
            outputs=viewed_components
        ).then(
            fn=self.update_preview_display,
            inputs=[self.edit_name_input],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview]
        ).then(
            fn=lambda: gr.update(visible=True), 
            inputs=[], 
            outputs=[self.box]
        )

        # Previous preview button
        self.button_prev_preview.click(
            fn=self.navigate_preview,
            inputs=[self.edit_name_input, self.preview_index_state, gr.State(-1)],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview],
            show_progress=False
        )

        # Next preview button
        self.button_next_preview.click(
            fn=self.navigate_preview,
            inputs=[self.edit_name_input, self.preview_index_state, gr.State(1)],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview],
            show_progress=False
        )

        # Delete preview button
        self.button_delete_preview.click(
            fn=self.delete_preview_by_index,
            inputs=[self.edit_name_input, self.preview_index_state],
            outputs=[self.html_preview, self.html_status],
            show_progress=False
        ).then(
            fn=self.update_preview_display,
            inputs=[self.edit_name_input],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview]
        ).then(
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
            inputs=[self.edit_name_input],
            outputs=[]
        )

        # Fetch from CivitAI button
        print("[DEBUG] Setting up fetch_from_civitai button handler")
        self.button_fetch_civitai.click(
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
            fn=self.update_preview_display,
            inputs=[self.edit_name_input],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview]
        ).then(
            # Refresh card in main view
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
            inputs=[self.edit_name_input],
            outputs=[]
        )

        # Save reorder button - single callback with JS
        self.button_save_reorder.click(
            fn=self.handle_reorder_and_refresh,
            _js="function(name, state) { var order = captureAndReturnReorderState(); console.log('JS captured order:', order); return [name, order]; }",
            inputs=[self.edit_name_input, self.reorder_new_order_state],
            outputs=[self.reorder_status, self.reorder_grid_html, self.html_preview],
            show_progress=True
        ).then(
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
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
            self.checkbox_pinned,  # NEW: pinned checkbox
        ]

        self.setup_save_handler(self.button_save, self.save_lora_user_metadata, edited_components)

    def setup_ui(self, gallery):
        """
        Override parent setup_ui to add gallery-based preview management.
        """
        # Add preview button (replaces the old "Replace preview")
        self.button_add_preview.click(
            fn=self.add_preview_from_gallery,
            _js=f"function(x, y, z){{return [selected_gallery_index_id('{self.tabname + '_gallery_container'}'), y, z]}}",
            inputs=[self.edit_name_input, gallery, self.edit_name_input],
            outputs=[self.html_preview, self.html_status],
            show_progress=False
        ).then(
            fn=self.update_preview_display,
            inputs=[self.edit_name_input],
            outputs=[self.html_preview, self.preview_index_state, self.preview_counter, self.button_delete_preview]
        ).then(
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
            inputs=[self.edit_name_input],
            outputs=[]
        )
