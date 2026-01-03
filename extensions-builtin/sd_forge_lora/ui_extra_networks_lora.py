import os

import network
import networks
from ui_edit_user_metadata import LoraUserMetadataEditor

from modules import shared, ui_extra_networks
from modules.ui_extra_networks import quote_js
import gradio as gr 


class ExtraNetworksPageLora(ui_extra_networks.ExtraNetworksPage):
    def __init__(self):
        super().__init__("Lora")
        self.allow_negative_prompt = True

    def refresh(self):
        networks.list_available_networks()

    def create_item(self, name, index=None, enable_filter=True):
        lora_on_disk = networks.available_networks.get(name)
        if lora_on_disk is None:
            return

        path, ext = os.path.splitext(lora_on_disk.filename)

        alias = lora_on_disk.get_alias()

        search_terms = [self.search_terms_from_path(lora_on_disk.filename)]
        if lora_on_disk.hash:
            search_terms.append(lora_on_disk.hash)
        # Get all preview images (for multiple preview support)
        all_previews = self.find_all_previews(path)
        
        # For backwards compatibility, also get single preview
        single_preview = self.find_preview(path) or self.find_embedded_preview(path, name, lora_on_disk.metadata)
        
        item = {
            "name": name,
            "filename": lora_on_disk.filename,
            "shorthash": lora_on_disk.shorthash,
            "preview": all_previews[0] if all_previews else single_preview,
            "preview_urls": all_previews,
            "description": self.find_description(path),
            "search_terms": search_terms,
            "local_preview": f"{path}.jpg",  # Force JPG for smaller file size
            "metadata": lora_on_disk.metadata,
            "sort_keys": {"default": index, **self.get_sort_keys(lora_on_disk.filename)},
        }

        self.read_user_metadata(item)
        activation_text = item["user_metadata"].get("activation text")
        preferred_weight = item["user_metadata"].get("preferred weight", 0.0)
        item["prompt"] = quote_js(f"<lora:{alias}:") + " + " + (str(preferred_weight) if preferred_weight else "opts.extra_networks_default_multiplier") + " + " + quote_js(">")

        if activation_text:
            item["prompt"] += " + " + quote_js(" " + activation_text)

        negative_prompt = item["user_metadata"].get("negative text", "")
        item["negative_prompt"] = quote_js(negative_prompt)

        # Add pinned status for sorting (AFTER read_user_metadata)
        pinned = item["user_metadata"].get("pinned", False)
        item["pinned"] = pinned
        item["sort_keys"]["pinned"] = 1 if pinned else 0

        #   filter displayed loras by UI setting
        sd_version = item["user_metadata"].get("sd version")
        if sd_version in network.SdVersion.__members__:
            item["sd_version"] = sd_version
            sd_version = network.SdVersion[sd_version]
        else:
            sd_version = lora_on_disk.sd_version  #   use heuristics
            # sd_version = network.SdVersion.Unknown     #   avoid heuristics

        item["sd_version_str"] = str(sd_version)

        return item

    def list_items(self):
        # instantiate a list to protect against concurrent modification
        names = list(networks.available_networks)
        for index, name in enumerate(names):
            item = self.create_item(name, index)
            if item is not None:
                yield item

    def allowed_directories_for_previews(self):
        return [shared.cmd_opts.lora_dir, *shared.cmd_opts.lora_dirs]

    def create_user_metadata_editor(self, ui, tabname):
        return LoraUserMetadataEditor(ui, tabname, self)

    def batch_fetch_civitai_metadata(self, tabname, search_text, current_dir, ui_preset, progress=gr.Progress()):
        """
        Batch fetch CivitAI metadata for filtered LoRAs only.
        
        Returns: (status_html, updated_names_json)
        """
        import time
        import re
        import json
        from modules import ui_extra_networks_user_metadata
        
        # Filter items based on current UI state
        filtered_items = []
        
        for name, item in self.items.items():
            # Apply search filter
            if search_text:
                search_terms = ' '.join(item.get('search_terms', [])).lower()
                item_name = item.get('name', '').lower()
                description = item.get('description', '').lower()
                
                search_words = search_text.lower().split()
                matches_search = all(
                    word in search_terms or word in item_name or word in description
                    for word in search_words
                )
                
                if not matches_search:
                    continue
            
            # Apply directory filter
            if current_dir:
                filename = item.get('filename', '')
                norm_filename = filename.replace('\\', '/').lower()
                norm_dir = current_dir.replace('\\', '/').lower()
                
                if norm_dir not in norm_filename:
                    continue
            
            # Apply UI preset filter (SD version)
            if ui_preset != "3":  # 3 = All
                sd_version = item.get('sd_version_str', 'SdVersion.Unknown')
                
                if ui_preset == "0" and sd_version != "SdVersion.SD1":
                    continue
                elif ui_preset == "1" and sd_version != "SdVersion.SDXL":
                    continue
                elif ui_preset == "2" and sd_version != "SdVersion.Flux":
                    continue
            
            filtered_items.append((name, item))
        
        if not filtered_items:
            return (
                "<div style='color: orange; padding: 10px;'>⚠️ No LoRAs match current filters</div>",
                "[]"
            )
        
        total = len(filtered_items)
        success_count = 0
        not_found_count = 0
        failed_count = 0
        updated_names = []  # Track successfully updated LoRAs
        
        temp_editor = ui_extra_networks_user_metadata.UserMetadataEditor(None, tabname, self)
        
        progress(0, desc=f"Starting batch fetch for {total} filtered LoRAs...")
        
        for i, (name, item) in enumerate(filtered_items):
            try:
                progress((i + 1) / total, desc=f"Processing {i + 1}/{total}: {name[:40]}...")
                
                result = temp_editor.fetch_from_civitai(name)
                
                if len(result) == 3:
                    description, notes, status_html = result
                    
                    if "✅" in status_html and not isinstance(description, dict):
                        user_metadata = temp_editor.get_user_metadata(name)
                        user_metadata["description"] = description
                        user_metadata["notes"] = notes
                        
                        # Extract LoRA-specific fields
                        if "Trained Words:" in notes:
                            match = re.search(r'Trained Words: (.+?)(?:\n|$)', notes)
                            if match:
                                user_metadata["activation text"] = match.group(1)
                        
                        if "Base Model:" in notes:
                            match = re.search(r'Base Model: (.+?)(?:\n|$)', notes)
                            if match:
                                base_model_str = match.group(1)
                                sd_version_map = {
                                    'SD 1.5': 'SD1', 'SD 1.4': 'SD1', 'SD 1': 'SD1',
                                    'SDXL 1.0': 'SDXL', 'SDXL 0.9': 'SDXL', 'SDXL Turbo': 'SDXL',
                                    'SDXL Lightning': 'SDXL', 'Pony': 'SDXL', 'Illustrious': 'SDXL',
                                    'Flux.1 D': 'Flux', 'Flux.1 S': 'Flux', 'Flux.1': 'Flux',
                                }
                                user_metadata["sd version"] = sd_version_map.get(base_model_str, 'Unknown')
                        
                        temp_editor.write_user_metadata(name, user_metadata)
                        success_count += 1
                        updated_names.append(name)  # Track this update
                        
                    elif "not found on CivitAI" in status_html or "not be uploaded" in status_html:
                        not_found_count += 1
                    else:
                        failed_count += 1
                else:
                    failed_count += 1
                
            except Exception as e:
                print(f"Error processing {name}: {e}")
                import traceback
                traceback.print_exc()
                failed_count += 1
            
            time.sleep(0.5)
        
        # Build status message
        status_html = "<div style='padding: 15px; background: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px; margin: 10px 0;'>"
        status_html += "<div style='font-size: 16px; font-weight: bold; margin-bottom: 10px;'>✅ Batch Fetch Complete</div>"
        status_html += f"<div><strong>Filtered LoRAs processed:</strong> {total}</div>"
        
        if success_count > 0:
            status_html += f"<div style='color: #28a745;'>✅ <strong>{success_count}</strong> successfully fetched and saved</div>"
        if not_found_count > 0:
            status_html += f"<div style='color: #ffc107;'>⚠️ <strong>{not_found_count}</strong> not found on CivitAI</div>"
        if failed_count > 0:
            status_html += f"<div style='color: #dc3545;'>❌ <strong>{failed_count}</strong> failed to fetch</div>"
        
        status_html += "<div style='margin-top: 10px; font-size: 12px; color: #666;'>Cards will refresh momentarily...</div>"
        status_html += "</div>"
        
        # Return status and list of updated LoRA names as JSON
        return status_html, json.dumps(updated_names)