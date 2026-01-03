import json
import gradio as gr

from modules import ui_extra_networks_user_metadata, sd_vae, shared
from modules.ui_components import ToolButton
from modules_forge import main_entry

refresh_symbol = '\U0001f504'  # 🔄

class CheckpointUserMetadataEditor(ui_extra_networks_user_metadata.UserMetadataEditor):
    def __init__(self, ui, tabname, page):
        super().__init__(ui, tabname, page)

        self.select_vae = None
        self.sd_version = 'Unknown'

    def fetch_from_civitai(self, name):
        """
        Override parent method to return Checkpoint-specific outputs.
        """
        print(f"[DEBUG Checkpoint] fetch_from_civitai called with name: {name}")
        
        # Call parent method to get base data
        parent_result = super().fetch_from_civitai(name)
        
        # Parent returns (description, notes, status) - 3 values
        # We need to return (description, notes, vae, sd_version, status) - 5 values
        
        if len(parent_result) == 3:
            description, notes, status = parent_result
            
            # If status indicates error, return error for all fields
            if "❌" in status or isinstance(description, dict):
                print(f"[DEBUG Checkpoint] Parent returned error, propagating")
                return (
                    description,
                    notes,
                    gr.update(),  # vae unchanged
                    gr.update(),  # sd_version unchanged
                    status
                )
            
            # Success case - extract SD version from notes
            print(f"[DEBUG Checkpoint] Parent successful, extracting checkpoint-specific data")
            
            sd_version = "Unknown"
            
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
                    print(f"[DEBUG Checkpoint] Extracted SD version: {sd_version}")
            
            return (
                description,
                notes,
                gr.update(),  # vae unchanged (user sets this manually)
                sd_version,
                status
            )
        else:
            print(f"[DEBUG Checkpoint] Unexpected parent return format: {len(parent_result)} values")
            return (
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                "<div style='color: red;'>Unexpected error</div>"
            )

    def save_user_metadata(self, name, desc, notes, vae, sd_version):
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["notes"] = notes
        user_metadata["vae_te"] = vae
        user_metadata["sd_version_str"] = 'SdVersion.' + sd_version

        self.write_user_metadata(name, user_metadata)

    def put_values_into_components(self, name):
        user_metadata = self.get_user_metadata(name)
        values = super().put_values_into_components(name)

        vae = user_metadata.get('vae_te', None)
        if vae is None:     # fallback to old type
            vae = user_metadata.get('vae', None)
            if vae is not None:
                if isinstance(vae, str):
                    vae = [vae]

        version = user_metadata.get('sd_version_str', '')
        if version == '':
            version = 'Unknown'
        else:
            version = version.replace('SdVersion.', '')

        return [
            *values[0:5],
            vae,
            version,
        ]

    def create_editor(self):    #happens before main_entry.modules_list is filled
        modules_list = ['Built in']
        if main_entry.module_list == {}:
            _, modules = main_entry.refresh_models()
            modules_list += list(modules)
        else:
            modules_list += list(main_entry.module_list.keys())

        def refreshModules ():
            return gr.update(choices=['Built in'] + list(main_entry.module_list.keys()))

        self.create_default_editor_elems()

        self.sd_version = gr.Radio(['SD1', 'SDXL', 'Flux', 'Unknown'], value='Unknown', label='Base model', interactive=True)

        with gr.Row():
            self.select_vae = gr.Dropdown(choices=modules_list, value=None, label="Preferred VAE / Text encoder(s)", elem_id="checpoint_edit_user_metadata_preferred_vae", multiselect=True)
            self.refresh = ToolButton(refresh_symbol)

            self.refresh.click(fn=refreshModules, outputs=self.select_vae, show_progress='hidden')

        self.edit_notes = gr.TextArea(label='Notes', lines=4)

        self.create_default_buttons()

        viewed_components = [
            self.edit_name,
            self.edit_description,
            self.html_filedata,
            self.html_preview,
            self.edit_notes,
            self.select_vae,
            self.sd_version,
        ]

        self.button_edit\
            .click(fn=self.put_values_into_components, inputs=[self.edit_name_input], outputs=viewed_components)\
            .then(fn=lambda: gr.update(visible=True), inputs=[], outputs=[self.box])

        # Connect fetch from CivitAI button
        print("[DEBUG Checkpoint] Setting up fetch_from_civitai button handler")
        self.button_fetch_civitai.click(
            fn=self.fetch_from_civitai,
            inputs=[self.edit_name_input],
            outputs=[
                self.edit_description,
                self.edit_notes,
                self.select_vae,
                self.sd_version,
                self.html_status
            ],
            show_progress=True
        ).then(
            # Refresh preview display after downloading images
            fn=lambda name: self.get_card_html(name),
            inputs=[self.edit_name_input],
            outputs=[self.html_preview]
        ).then(
            # Refresh card in main view
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
            inputs=[self.edit_name_input],
            outputs=[]
        )

        edited_components = [
            self.edit_description,
            self.edit_notes,
            self.select_vae,
            self.sd_version,
        ]

        self.setup_save_handler(self.button_save, self.save_user_metadata, edited_components)
