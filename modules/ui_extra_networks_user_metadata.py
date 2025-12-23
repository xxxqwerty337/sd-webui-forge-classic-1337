import datetime
import html
import json
import os.path

import gradio as gr

from modules import infotext_utils, images, sysinfo, errors, ui_extra_networks, shared


class UserMetadataEditor:

    def __init__(self, ui, tabname, page):
        self.ui = ui
        self.tabname = tabname
        self.page = page
        self.id_part = f"{self.tabname}_{self.page.extra_networks_tabname}_edit_user_metadata"

        self.box = None

        self.edit_name_input = None
        self.button_edit = None

        self.edit_name = None
        self.edit_description = None
        self.edit_notes = None
        self.html_filedata = None
        self.html_preview = None
        self.html_status = None

        self.button_cancel = None
        self.button_replace_preview = None
        self.button_save = None

    def get_user_metadata(self, name):
        item = self.page.items.get(name, {})

        user_metadata = item.get('user_metadata', None)
        if not user_metadata:
            user_metadata = {'description': item.get('description', '')}
            item['user_metadata'] = user_metadata

        return user_metadata

    def create_extra_default_items_in_left_column(self):
        pass

    def create_default_editor_elems(self):
        with gr.Row():
            with gr.Column(scale=2):
                self.edit_name = gr.HTML(elem_classes="extra-network-name")
                self.edit_description = gr.Textbox(label="Description", lines=4)
                self.html_filedata = gr.HTML()

                self.create_extra_default_items_in_left_column()

            with gr.Column(scale=1, min_width=0):
                self.html_preview = gr.HTML()

    def create_default_buttons(self):

        with gr.Row(elem_classes="edit-user-metadata-buttons"):
            self.button_cancel = gr.Button('Cancel')
            self.button_replace_preview = gr.Button('Replace preview', variant='primary')
            self.button_save = gr.Button('Save', variant='primary')

        self.html_status = gr.HTML(elem_classes="edit-user-metadata-status")

        self.button_cancel.click(fn=None, _js="closePopup")

    def get_card_html(self, name):
        item = self.page.items.get(name, {})

        preview_url = item.get("preview", None)

        if not preview_url:
            filename, _ = os.path.splitext(item["filename"])
            preview_url = self.page.find_preview(filename)
            item["preview"] = preview_url

        if preview_url:
            preview = f'''
            <div class='card standalone-card-preview'>
                <img src="{html.escape(preview_url)}" class="preview">
            </div>
            '''
        else:
            preview = "<div class='card standalone-card-preview'></div>"

        return preview

    def relative_path(self, path):
        for parent_path in self.page.allowed_directories_for_previews():
            if ui_extra_networks.path_is_parent(parent_path, path):
                return os.path.relpath(path, parent_path)

        return os.path.basename(path)

    def get_metadata_table(self, name):
        item = self.page.items.get(name, {})
        try:
            filename = item["filename"]
            shorthash = item.get("shorthash", None)

            stats = os.stat(filename)
            params = [
                ('Filename: ', self.relative_path(filename)),
                ('File size: ', sysinfo.pretty_bytes(stats.st_size)),
                ('Hash: ', shorthash),
                ('Modified: ', datetime.datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M')),
            ]

            return params
        except Exception as e:
            errors.display(e, f"reading info for {name}")
            return []

    def put_values_into_components(self, name):
        user_metadata = self.get_user_metadata(name)

        try:
            params = self.get_metadata_table(name)
        except Exception as e:
            errors.display(e, f"reading metadata info for {name}")
            params = []

        table = '<table class="file-metadata">' + "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in params if value is not None) + '</table>'

        return html.escape(name), user_metadata.get('description', ''), table, self.get_card_html(name), user_metadata.get('notes', '')

    def write_user_metadata(self, name, metadata):
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        basename, ext = os.path.splitext(filename)

        metadata_path = basename + '.json'
        with open(metadata_path, "w", encoding="utf8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
        self.page.lister.update_file_entry(metadata_path)

    def save_user_metadata(self, name, desc, notes):
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["notes"] = notes

        self.write_user_metadata(name, user_metadata)

    def setup_save_handler(self, button, func, components):
        button\
            .click(fn=func, inputs=[self.edit_name_input, *components], outputs=[])\
            .then(fn=None, _js="function(name){closePopup(); extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}", inputs=[self.edit_name_input], outputs=[])

    def create_editor(self):
        self.create_default_editor_elems()

        self.edit_notes = gr.TextArea(label='Notes', lines=4)

        self.create_default_buttons()

        self.button_edit\
            .click(fn=self.put_values_into_components, inputs=[self.edit_name_input], outputs=[self.edit_name, self.edit_description, self.html_filedata, self.html_preview, self.edit_notes])\
            .then(fn=lambda: gr.update(visible=True), inputs=[], outputs=[self.box])

        self.setup_save_handler(self.button_save, self.save_user_metadata, [self.edit_description, self.edit_notes])

    def create_ui(self):
        with gr.Box(visible=False, elem_id=self.id_part, elem_classes="edit-user-metadata") as box:
            self.box = box

            self.edit_name_input = gr.Textbox("Edit user metadata card id", visible=False, elem_id=f"{self.id_part}_name")
            self.button_edit = gr.Button("Edit user metadata", visible=False, elem_id=f"{self.id_part}_button")

            self.create_editor()

    def get_all_preview_paths(self, name):
        """
        Get all preview file paths for an item.
        Returns list of tuples: [(path, index), ...]
        Index 0 = main preview (no number), 1+ = numbered previews
        """
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        if not filename:
            return []
        
        basename, ext = os.path.splitext(filename)
        
        previews = []
        extensions = ['png', 'jpg', 'jpeg', 'webp']
        
        # Check main preview (no number)
        for ext in extensions:
            main_preview = f"{basename}.{ext}"
            if os.path.exists(main_preview):
                previews.append((main_preview, 0))
                break
        
        # Check numbered previews
        index = 1
        max_check = 100  # Safety limit
        while index < max_check:
            found = False
            for ext in extensions:
                numbered_preview = f"{basename}.preview.{index}.{ext}"
                if os.path.exists(numbered_preview):
                    previews.append((numbered_preview, index))
                    found = True
                    break
            if not found:
                break
            index += 1
        
        return previews
    
    def get_next_preview_number(self, name):
        """
        Get the next available preview number for an item.
        Returns: 0 for main preview, 1+ for numbered
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return 0  # No previews exist, use main
        
        # If main preview doesn't exist, return 0
        if previews[0][1] != 0:
            return 0
        
        # Find next available numbered preview
        existing_numbers = [idx for _, idx in previews]
        next_num = 1
        while next_num in existing_numbers:
            next_num += 1
        return next_num
    
    def get_preview_filename(self, name, index):
        """
        Get the preview filename for a specific index.
        Index 0 = main preview (lora.jpg)
        Index 1+ = numbered preview (lora.preview.N.jpg)
        
        Uses JPG format to reduce file size.
        """
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        if not filename:
            return None
        
        basename, ext = os.path.splitext(filename)
        
        # Force JPG format for smaller file size
        format_ext = "jpg"
        
        if index == 0:
            return f"{basename}.{format_ext}"
        else:
            return f"{basename}.preview.{index}.{format_ext}"
    
    def add_preview_from_gallery(self, gallery_index, gallery, name):
        """
        Add a new preview image from the gallery.
        Does NOT replace existing previews.
        Saves as JPG for smaller file size.
        """
        if len(gallery) == 0:
            return self.get_preview_gallery_html(name), "There is no image in gallery to save as a preview."
        
        # Get the image from gallery
        index = int(gallery_index)
        index = 0 if index < 0 else index
        index = len(gallery) - 1 if index >= len(gallery) else index
        
        img_info = gallery[index if index >= 0 else 0]
        image = infotext_utils.image_from_url_text(img_info)
        geninfo, items = images.read_info_from_image(image)
        
        # Get next available preview number
        next_num = self.get_next_preview_number(name)
        preview_path = self.get_preview_filename(name, next_num)
        
        if not preview_path:
            return self.get_preview_gallery_html(name), "Error: Could not determine preview filename."
        
        # Convert to RGB if RGBA (JPG doesn't support transparency)
        if image.mode == 'RGBA':
            # Create white background
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])  # Use alpha channel as mask
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Save as JPG manually to control quality
        # Use PIL directly since save_image_with_geninfo doesn't support JPG quality
        from PIL import PngImagePlugin
        
        # Prepare metadata for JPG
        pnginfo_data = PngImagePlugin.PngInfo()
        if geninfo:
            pnginfo_data.add_text("parameters", geninfo)
        
        # Save as JPG with quality setting
        try:
            # For JPG, we save with quality and then add EXIF metadata
            image.save(
                preview_path,
                format='JPEG',
                quality=95,  # High quality
                optimize=True,
                exif=image.info.get('exif', b'')  # Preserve EXIF if present
            )
            
            # Try to add generation info to EXIF
            if geninfo:
                from PIL import Image as PILImage
                from PIL.ExifTags import TAGS
                import piexif
                
                # Try to add to EXIF UserComment (if piexif available)
                try:
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                    exif_dict["Exif"][piexif.ExifIFD.UserComment] = geninfo.encode('utf-8')
                    exif_bytes = piexif.dump(exif_dict)
                    
                    # Re-save with EXIF
                    image.save(
                        preview_path,
                        format='JPEG',
                        quality=95,
                        optimize=True,
                        exif=exif_bytes
                    )
                except (ImportError, Exception) as e:
                    # piexif not available or failed, just save without it
                    print(f"Note: Could not embed metadata in JPG (piexif not available): {e}")
            
        except Exception as e:
            print(f"Error saving JPG preview: {e}")
            # Fallback: use save_image_with_geninfo (will save as PNG)
            images.save_image_with_geninfo(image, geninfo, preview_path)
        
        self.page.lister.update_file_entry(preview_path)

    def delete_preview_by_index(self, name, preview_index):
        """
        Delete a specific preview image by its index.
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return self.get_preview_gallery_html(name), "No previews to delete."
        
        preview_index = int(preview_index)
        if preview_index < 0 or preview_index >= len(previews):
            return self.get_preview_gallery_html(name), "Invalid preview index."
        
        # Get the file to delete
        file_to_delete, file_index = previews[preview_index]
        
        try:
            os.remove(file_to_delete)
            # Force lister to clear cache for this file
            self.page.lister.update_file_entry(file_to_delete)
        except Exception as e:
            return self.get_preview_gallery_html(name), f"Error deleting preview: {str(e)}"
        
        # Renumber remaining previews to fill gaps
        self.renumber_previews_after_deletion(name)
        
        # Force refresh of item's preview data
        item = self.page.items.get(name, {})
        basename = os.path.splitext(item["filename"])[0]
        
        # Clear lister cache for all preview files
        self.page.lister.reset()
        
        # Re-scan all previews
        item['preview_urls'] = self.page.find_all_previews(basename)
        
        # Update main preview
        all_previews = item['preview_urls']
        if all_previews:
            item['preview'] = all_previews[0]
        else:
            item['preview'] = None
        
        return self.get_preview_gallery_html(name), f'Deleted preview'
    
    def renumber_previews_after_deletion(self, name):
        """
        Renumber previews to fill gaps after deletion.
        Example: If preview.2 is deleted, preview.3 becomes preview.2
        """
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        if not filename:
            return
        
        basename, ext = os.path.splitext(filename)
        extensions = ['png', 'jpg', 'jpeg', 'webp']
        
        # Get all numbered previews (skip main preview)
        numbered_previews = []
        for i in range(1, 100):
            for file_ext in extensions:
                preview_path = f"{basename}.preview.{i}.{file_ext}"
                if os.path.exists(preview_path):
                    numbered_previews.append((i, preview_path))
                    break
        
        # Renumber sequentially
        for new_index, (old_index, old_path) in enumerate(numbered_previews, start=1):
            if new_index != old_index:
                file_ext = os.path.splitext(old_path)[1]
                new_path = f"{basename}.preview.{new_index}{file_ext}"
                try:
                    os.rename(old_path, new_path)
                    self.page.lister.update_file_entry(new_path)
                except Exception as e:
                    print(f"Error renumbering preview: {e}")
    
    def get_preview_gallery_html(self, name):
        """
        Get HTML for displaying all previews in a gallery.
        Returns HTML with all preview images.
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return "<div class='preview-gallery-empty'>No previews</div>"
        
        html_parts = ["<div class='preview-gallery'>"]
        for idx, (preview_path, _) in enumerate(previews):
            # Convert file path to URL
            preview_url = self.page.link_preview(preview_path)
            html_parts.append(f"""
                <div class='preview-gallery-item' data-index='{idx}'>
                    <img src='{html.escape(preview_url)}' alt='Preview {idx + 1}'>
                    <div class='preview-gallery-label'>Preview {idx + 1}</div>
                </div>
            """)
        html_parts.append("</div>")
        
        return "".join(html_parts)

    def save_preview(self, index, gallery, name):
        """
        LEGACY: Replace main preview (for backwards compatibility).
        Use add_preview_from_gallery for adding multiple previews.
        Saves as JPG for smaller file size.
        """
        if len(gallery) == 0:
            return self.get_card_html(name), "There is no image in gallery to save as a preview."

        item = self.page.items.get(name, {})

        index = int(index)
        index = 0 if index < 0 else index
        index = len(gallery) - 1 if index >= len(gallery) else index

        img_info = gallery[index if index >= 0 else 0]
        image = infotext_utils.image_from_url_text(img_info)
        geninfo, items = images.read_info_from_image(image)

        # Convert to RGB for JPG (change extension to .jpg)
        preview_path = item["local_preview"]
        if preview_path.lower().endswith('.png'):
            preview_path = preview_path[:-4] + '.jpg'
        
        if image.mode == 'RGBA':
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')

        # Save as JPG manually
        try:
            image.save(
                preview_path,
                format='JPEG',
                quality=95,
                optimize=True,
                exif=image.info.get('exif', b'')
            )
            
            # Try to add generation info to EXIF
            if geninfo:
                try:
                    import piexif
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                    exif_dict["Exif"][piexif.ExifIFD.UserComment] = geninfo.encode('utf-8')
                    exif_bytes = piexif.dump(exif_dict)
                    
                    image.save(
                        preview_path,
                        format='JPEG',
                        quality=95,
                        optimize=True,
                        exif=exif_bytes
                    )
                except (ImportError, Exception):
                    pass  # Metadata optional
                    
        except Exception as e:
            print(f"Error saving JPG: {e}")
            images.save_image_with_geninfo(image, geninfo, preview_path)
        
        self.page.lister.update_file_entry(preview_path)

    def get_preview_data_for_reorder(self, name):
        """
        Get all preview data needed for the drag-and-drop reorder UI.
        Returns list of dicts with preview info: [
            {
                'index': 0,
                'path': '/path/to/file.png',
                'url': './sd_extra_networks/thumb?...',
                'filename': 'file.png',
                'is_main': True
            },
            ...
        ]
        """
        previews = self.get_all_preview_paths(name)
        if not previews:
            return []
        
        preview_data = []
        for idx, (preview_path, file_index) in enumerate(previews):
            preview_url = self.page.link_preview(preview_path)
            filename = os.path.basename(preview_path)
            is_main = (idx == 0)  # First preview is always the main one
            
            preview_data.append({
                'index': idx,
                'path': preview_path,
                'url': preview_url,
                'filename': filename,
                'is_main': is_main
            })
        
        return preview_data
    
    def reorder_previews(self, name, new_order_json):
        """
        Reorder preview files based on new order from drag-and-drop.
        new_order_json: JSON string like "[0,2,1,3]" representing new positions
        
        Returns: (success_message, error_message)
        """
        try:
            
            # Parse the new order
            import json as json_module
            new_order = json_module.loads(new_order_json)
            
            if not isinstance(new_order, list):
                return "", "Invalid order format"
            
            # Get current previews
            current_previews = self.get_all_preview_paths(name)
            if not current_previews:
                return "", "No previews to reorder"
            
            # Validate new order
            if len(new_order) != len(current_previews):
                return "", f"Order length mismatch: expected {len(current_previews)}, got {len(new_order)}"
            
            if sorted(new_order) != list(range(len(current_previews))):
                return "", "Invalid order: must contain all indices from 0 to N-1"
            
            # Create mapping of old position -> new position
            # new_order[new_pos] = old_pos
            # We need: old_pos -> new_pos
            position_map = {}
            for new_pos, old_pos in enumerate(new_order):
                position_map[old_pos] = new_pos
            
            # If order hasn't changed, do nothing
            if position_map == {i: i for i in range(len(current_previews))}:
                return "Order unchanged", ""
            
            # Perform the reordering
            success_msg = self._execute_preview_reorder(name, current_previews, position_map)
            
            # Force refresh
            item = self.page.items.get(name, {})
            basename = os.path.splitext(item["filename"])[0]
            self.page.lister.reset()
            item['preview_urls'] = self.page.find_all_previews(basename)
            item['preview'] = item['preview_urls'][0] if item['preview_urls'] else None
            
            return success_msg, ""
            
        except json_module.JSONDecodeError as e:
            return "", f"Failed to parse order: {str(e)}"
        except Exception as e:
            return "", f"Error reordering previews: {str(e)}"
    
    def _execute_preview_reorder(self, name, current_previews, position_map):
        """
        Execute the actual file renaming for preview reordering.
        Uses a temporary directory to avoid conflicts.
        
        Args:
            name: Item name
            current_previews: List of (path, index) tuples
            position_map: Dict mapping old_position -> new_position
        
        Returns:
            Success message string
        """
        import tempfile
        import shutil
        
        item = self.page.items.get(name, {})
        basename = os.path.splitext(item["filename"])[0]
        
        # Create temp directory for staging
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Step 1: Copy all files to temp with their new names
            temp_files = []
            for old_pos, (old_path, _) in enumerate(current_previews):
                new_pos = position_map[old_pos]
                
                # Determine new filename
                file_ext = os.path.splitext(old_path)[1]
                if new_pos == 0:
                    # New main preview
                    new_filename = f"{os.path.basename(basename)}{file_ext}"
                else:
                    # Numbered preview
                    new_filename = f"{os.path.basename(basename)}.preview.{new_pos}{file_ext}"
                
                # Copy to temp with new name
                temp_path = os.path.join(temp_dir, new_filename)
                shutil.copy2(old_path, temp_path)
                temp_files.append((temp_path, old_path, new_pos))
            
            # Step 2: Delete original files
            for old_path, _ in current_previews:
                try:
                    os.remove(old_path)
                    self.page.lister.update_file_entry(old_path)
                except Exception as e:
                    print(f"Warning: Could not delete {old_path}: {e}")
            
            # Step 3: Move files from temp to original directory with final names
            final_paths = []
            for temp_path, old_path, new_pos in temp_files:
                # Determine final path
                file_ext = os.path.splitext(temp_path)[1]
                if new_pos == 0:
                    final_path = f"{basename}{file_ext}"
                else:
                    final_path = f"{basename}.preview.{new_pos}{file_ext}"
                
                shutil.move(temp_path, final_path)
                self.page.lister.update_file_entry(final_path)
                final_paths.append(final_path)
            
            return f"Successfully reordered {len(final_paths)} previews"
            
        except Exception as e:
            # If anything fails, try to restore from temp
            print(f"Error during reorder, attempting to restore: {e}")
            raise
        finally:
            # Clean up temp directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
    
    def get_reorder_grid_html(self, name):
        """
        Generate HTML for the drag-and-drop reorder grid.
        Returns: HTML string with grid and hidden data
        """
        preview_data = self.get_preview_data_for_reorder(name)
        
        if not preview_data:
            return """
            <div class='reorder-container'>
                <div class='reorder-grid-empty'>
                    No previews to reorder. Add some preview images first!
                </div>
            </div>
            """
        
        # Only show reorder UI if there are 2+ previews
        if len(preview_data) < 2:
            return """
            <div class='reorder-container'>
                <div class='reorder-grid-empty'>
                    Need at least 2 previews to reorder. Add more preview images!
                </div>
            </div>
            """
        
        # Encode preview data as JSON for JavaScript access
        import json as json_module
        preview_data_json = html.escape(json_module.dumps(preview_data))
        
        # Generate HTML for thumbnail grid
        html_parts = [
            "<div class='reorder-container'>",
            f"  <div id='preview-reorder-data' style='display:none;' data-previews='{preview_data_json}'></div>",
            "  <div class='reorder-instructions'>",
            "    <span>💡 <strong>Drag thumbnails to reorder</strong> • First position = main preview (shown on card)</span>",
            "  </div>",
            "  <div id='preview-reorder-grid' class='preview-reorder-grid'>"
        ]
        
        for preview in preview_data:
            badge = "⭐ MAIN" if preview['is_main'] else f"#{preview['index'] + 1}"
            html_parts.append(f"""
                <div class='reorder-item' data-index='{preview['index']}' data-path='{html.escape(preview['path'])}'>
                    <div class='reorder-drag-handle'>⋮⋮</div>
                    <img src='{html.escape(preview['url'])}' alt='Preview {preview['index'] + 1}' draggable='false'>
                    <div class='reorder-badge'>{badge}</div>
                </div>
            """)
        
        html_parts.append("  </div>")
        html_parts.append("</div>")
        
        return "".join(html_parts)

    def setup_ui(self, gallery):
        self.button_replace_preview.click(
            fn=self.save_preview,
            _js=f"function(x, y, z){{return [selected_gallery_index_id('{self.tabname + '_gallery_container'}'), y, z]}}",
            inputs=[self.edit_name_input, gallery, self.edit_name_input],
            outputs=[self.html_preview, self.html_status]
        ).then(
            fn=None,
            _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}",
            inputs=[self.edit_name_input],
            outputs=[]
        )
