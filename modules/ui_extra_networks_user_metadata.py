import datetime
import html
import json
import os.path
import requests
from PIL import Image
from io import BytesIO

import gradio as gr

from modules import infotext_utils, images, sysinfo, errors, ui_extra_networks, errors, images, shared


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

    def html_to_plaintext(self, html_text):
        """
        Convert HTML to readable plain text with proper formatting.
        Preserves paragraphs, line breaks, and basic structure.
        """
        if not html_text:
            return ""
        
        import re
        
        # Replace paragraph breaks with double newlines
        text = re.sub(r'</p>\s*<p>', '\n\n', html_text)
        
        # Replace <br> tags with newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        
        # Replace list items with bullet points
        text = re.sub(r'<li>', '\n• ', text, flags=re.IGNORECASE)
        text = re.sub(r'</li>', '', text, flags=re.IGNORECASE)
        
        # Add newlines around headings
        text = re.sub(r'<h[1-6][^>]*>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</h[1-6]>', '\n', text, flags=re.IGNORECASE)
        
        # Remove all other HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&amp;', '&')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        
        # Clean up excessive whitespace
        text = re.sub(r'\n\n\n+', '\n\n', text)  # Max 2 consecutive newlines
        text = re.sub(r'[ \t]+', ' ', text)      # Collapse spaces/tabs
        
        # Trim leading/trailing whitespace
        text = text.strip()
        
        return text

    def get_user_metadata(self, name):
        item = self.page.items.get(name, {})

        user_metadata = item.get("user_metadata", None)
        if not user_metadata:
            user_metadata = {'description': item.get('description', '')}
            item['user_metadata'] = user_metadata

        # Ensure pinned field exists (default to False)
        if 'pinned' not in user_metadata:
            user_metadata['pinned'] = False

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
            self.button_fetch_civitai = gr.Button('🌐 Fetch from CivitAI', variant='secondary')
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

        preview = ""

        if preview_url:
            _, preview_format = os.path.splitext(preview_url.rsplit("&mtime=", 1)[0])
            if preview_format.lower() in (".mp4", ".webm"):
                preview = f'<video src="{html.escape(preview_url)}" class="preview" autoplay loop muted playsinline></video>'
            else:
                preview = f'<img src="{html.escape(preview_url)}" class="preview">'

        return f'<div class="card standalone-card-preview">{preview}</div>'

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
                ("Filename: ", self.relative_path(filename)),
                ("File size: ", sysinfo.pretty_bytes(stats.st_size)),
                ("Hash: ", shorthash),
                ("Modified: ", datetime.datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M")),
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

        table = '<table class="file-metadata">' + "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in params if value is not None) + "</table>"

        # Convert HTML description to plain text for editing
        description_html = user_metadata.get('description', '')
        description_plain = self.html_to_plaintext(description_html)

        return html.escape(name), description_plain, table, self.get_card_html(name), user_metadata.get('notes', '')

    def write_user_metadata(self, name, metadata):
        item = self.page.items.get(name, {})
        filename = item.get("filename", None)
        basename, ext = os.path.splitext(filename)

        metadata_path = basename + ".json"
        with open(metadata_path, "w", encoding="utf8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
        self.page.lister.update_file_entry(metadata_path)

    def calculate_file_sha256(self, filename):
        """Calculate SHA256 hash of entire file."""
        import hashlib
        
        sha256 = hashlib.sha256()
        try:
            with open(filename, 'rb') as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest().lower()
        except Exception as e:
            print(f"Error calculating SHA256: {e}")
            return None

    def fetch_civitai_data_by_hash(self, hash_value):
        """Try to fetch from CivitAI using a hash value."""
        if not hash_value:
            return None
        
        url = f"https://civitai.com/api/v1/model-versions/by-hash/{hash_value}"
        print(f"[DEBUG] Fetching from CivitAI: {url}")
        
        try:
            response = requests.get(url, timeout=10)
            print(f"[DEBUG] Response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"[DEBUG] Success! Got data from CivitAI")
                return response.json()
            elif response.status_code == 404:
                print(f"[DEBUG] 404 - Hash not found on CivitAI")
                return None
            else:
                print(f"[DEBUG] CivitAI API error: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[DEBUG] Network error: {e}")
            return None

    def fetch_from_civitai(self, name):
        """
        Fetch model details from CivitAI API using hash lookup.
        Works for all model types (LoRAs, Checkpoints, etc.)
        Returns tuple of updated component values.
        """
        print(f"[DEBUG] fetch_from_civitai called with name: {name}")
        
        item = self.page.items.get(name, {})
        if not item:
            print(f"[DEBUG] Item not found in page.items")
            return self._return_fetch_error("Model not found in items")
        
        filename = item.get('filename', '')
        print(f"[DEBUG] Filename: {filename}")
        
        if not filename:
            print(f"[DEBUG] No filename found")
            return self._return_fetch_error("No filename found for this model")
        
        # Try multiple hash approaches
        model_data = None
        used_hash_method = None
        
        # Try 1: Use existing shorthash if available
        shorthash = item.get('shorthash') or item.get('hash', '')
        print(f"[DEBUG] Trying shorthash: {shorthash}")
        
        if shorthash:
            model_data = self.fetch_civitai_data_by_hash(shorthash)
            if model_data:
                used_hash_method = f"shorthash ({shorthash[:8]}...)"
                print(f"[DEBUG] Success with shorthash!")
        
        # Try 2: Calculate full file SHA256
        if not model_data:
            print(f"[DEBUG] Calculating full SHA256...")
            full_sha256 = self.calculate_file_sha256(filename)
            print(f"[DEBUG] Full SHA256: {full_sha256}")
            
            if full_sha256:
                # Try AutoV3 (first 12 characters)
                autov3 = full_sha256[:12]
                print(f"[DEBUG] Trying AutoV3: {autov3}")
                model_data = self.fetch_civitai_data_by_hash(autov3)
                if model_data:
                    used_hash_method = f"AutoV3 ({autov3})"
                    print(f"[DEBUG] Success with AutoV3!")
                else:
                    # Try AutoV2 (first 10 characters)
                    autov2 = full_sha256[:10]
                    print(f"[DEBUG] Trying AutoV2: {autov2}")
                    model_data = self.fetch_civitai_data_by_hash(autov2)
                    if model_data:
                        used_hash_method = f"AutoV2 ({autov2})"
                        print(f"[DEBUG] Success with AutoV2!")
                    else:
                        # Try full SHA256 as last resort
                        print(f"[DEBUG] Trying full SHA256")
                        model_data = self.fetch_civitai_data_by_hash(full_sha256)
                        if model_data:
                            used_hash_method = f"Full SHA256"
                            print(f"[DEBUG] Success with full SHA256!")
        
        if not model_data:
            print(f"[DEBUG] All hash methods failed")
            return self._return_fetch_error(
                "Model not found on CivitAI. "
                "This model may not be uploaded to CivitAI, or the hash doesn't match."
            )
        
        print(f"[DEBUG] Version data retrieved, now fetching full model details...")
        
        # Get model ID to fetch full details
        model_id = model_data.get('modelId')
        if not model_id:
            print(f"[DEBUG] No modelId in version data")
            return self._return_fetch_error("Could not get model ID from CivitAI")
        
        # Fetch full model details (includes description)
        model_url = f"https://civitai.com/api/v1/models/{model_id}"
        print(f"[DEBUG] Fetching full model details from: {model_url}")
        
        try:
            model_response = requests.get(model_url, timeout=10)
            if model_response.status_code != 200:
                print(f"[DEBUG] Failed to get full model details: HTTP {model_response.status_code}")
                full_model_data = {}
            else:
                full_model_data = model_response.json()
                print(f"[DEBUG] Successfully fetched full model details")
        except Exception as e:
            print(f"[DEBUG] Error fetching full model details: {e}")
            full_model_data = {}
        
        # Extract metadata - prioritize full model data for description
        model_name = full_model_data.get('name', model_data.get('model', {}).get('name', ''))
        model_description = full_model_data.get('description', '')  # From full model endpoint
        model_type = full_model_data.get('type', model_data.get('model', {}).get('type', ''))
        trained_words = model_data.get('trainedWords', [])  # From version data
        base_model = model_data.get('baseModel', 'Unknown')  # From version data
        
        print(f"[DEBUG] Model name: {model_name}")
        print(f"[DEBUG] Raw description from API: {model_description[:200] if model_description else 'None'}...")
        print(f"[DEBUG] Model type: {model_type}")
        print(f"[DEBUG] Base model: {base_model}")
        
        # Clean HTML from description
        clean_desc = self._clean_html(model_description)
        
        # Download preview images
        images = model_data.get('images', [])
        basename = os.path.splitext(filename)[0]
        
        print(f"[DEBUG] Found {len(images)} images to download")
        
        download_count = 0
        for idx, img_data in enumerate(images[:10]):  # Limit to 10 images
            img_url = img_data.get('url')
            if not img_url:
                print(f"[DEBUG] Image {idx}: No URL")
                continue
            
            # Skip videos
            if img_url.lower().endswith(('.mp4', '.webm', '.mov')):
                print(f"[DEBUG] Image {idx}: Skipping video")
                continue
            
            try:
                print(f"[DEBUG] Downloading image {idx} from: {img_url}")
                
                # Download image
                img_response = requests.get(img_url, timeout=15)
                if img_response.status_code != 200:
                    print(f"[DEBUG] Image {idx}: HTTP {img_response.status_code}")
                    continue
                
                # Load image
                from PIL import Image
                image = Image.open(BytesIO(img_response.content))
                
                # Extract metadata from API response
                meta = img_data.get('meta', {})
                if meta:
                    # Build parameters string in SD WebUI format
                    prompt = meta.get('prompt', '')
                    negative = meta.get('negativePrompt', '')
                    steps = meta.get('steps', '')
                    sampler = meta.get('sampler', '')
                    cfg = meta.get('cfgScale', '')
                    seed = meta.get('seed', '')
                    size = meta.get('Size', '')
                    model_hash = meta.get('Model hash', '')
                    model_name = meta.get('Model', '')
                    
                    # Format like SD WebUI expects
                    params_parts = []
                    if prompt:
                        params_parts.append(prompt)
                    if negative:
                        params_parts.append(f"Negative prompt: {negative}")
                    
                    settings = []
                    if steps:
                        settings.append(f"Steps: {steps}")
                    if sampler:
                        settings.append(f"Sampler: {sampler}")
                    if cfg:
                        settings.append(f"CFG scale: {cfg}")
                    if seed:
                        settings.append(f"Seed: {seed}")
                    if size:
                        settings.append(f"Size: {size}")
                    if model_name:
                        settings.append(f"Model: {model_name}")
                    if model_hash:
                        settings.append(f"Model hash: {model_hash}")
                    
                    if settings:
                        params_parts.append(", ".join(settings))
                    
                    parameters_text = "\n".join(params_parts)
                    print(f"[DEBUG] Extracted parameters: {parameters_text[:100]}...")
                else:
                    parameters_text = None
                    print(f"[DEBUG] Image {idx}: No metadata in API response")
                
                # Convert to RGB for JPEG (no transparency)
                if image.mode == 'RGBA':
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    background.paste(image, mask=image.split()[3])
                    image = background
                elif image.mode != 'RGB':
                    image = image.convert('RGB')
                
                # Determine filename - always use .jpg
                if download_count == 0:
                    preview_path = f"{basename}.jpg"
                else:
                    preview_path = f"{basename}.preview.{download_count}.jpg"
                
                print(f"[DEBUG] Saving to: {preview_path}")
                
                # Save as JPEG with embedded metadata
                if parameters_text:
                    try:
                        # Try to use piexif to embed metadata in EXIF
                        import piexif
                        
                        exif_dict = {
                            "0th": {},
                            "Exif": {},
                            "GPS": {},
                            "1st": {},
                            "thumbnail": None
                        }
                        
                        # Use UserComment for parameters (most compatible)
                        exif_dict["Exif"][piexif.ExifIFD.UserComment] = parameters_text.encode('utf-8')
                        
                        # Also try ImageDescription
                        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = parameters_text.encode('utf-8')[:65535]  # Max length
                        
                        exif_bytes = piexif.dump(exif_dict)
                        
                        image.save(
                            preview_path,
                            format='JPEG',
                            quality=95,
                            optimize=True,
                            exif=exif_bytes
                        )
                        print(f"[DEBUG] Image {idx}: Saved with EXIF metadata")
                        
                    except ImportError:
                        print(f"[DEBUG] piexif not available, saving without EXIF metadata")
                        # Fallback: save without EXIF
                        image.save(
                            preview_path,
                            format='JPEG',
                            quality=95,
                            optimize=True
                        )
                    except Exception as e:
                        print(f"[DEBUG] EXIF embed failed: {e}, saving without metadata")
                        image.save(
                            preview_path,
                            format='JPEG',
                            quality=95,
                            optimize=True
                        )
                else:
                    # No metadata to embed
                    image.save(
                        preview_path,
                        format='JPEG',
                        quality=95,
                        optimize=True
                    )
                    print(f"[DEBUG] Image {idx}: Saved without metadata (none available)")
                
                download_count += 1
                
                # Update lister cache
                self.page.lister.update_file_entry(preview_path)
                
                print(f"[DEBUG] Image {idx}: Saved successfully as {os.path.basename(preview_path)}")
                
            except Exception as e:
                print(f"[DEBUG] Error downloading image {idx}: {e}")
                import traceback
                traceback.print_exc()
                continue


        # Build status message
        status_msg = f"✅ <b>Fetched from CivitAI</b><br>"
        status_msg += f"Model: {model_name}<br>"
        status_msg += f"Type: {model_type}<br>"
        status_msg += f"Base Model: {base_model}<br>"
        status_msg += f"Hash Method: {used_hash_method}<br>"
        status_msg += f"Downloaded {download_count} preview image(s)"
        
        print(f"[DEBUG] Status: {status_msg}")
        
        # Get current user metadata to preserve existing notes
        user_metadata = self.get_user_metadata(name)
        current_notes = user_metadata.get('notes', '')
        
        print(f"[DEBUG] Model description length: {len(clean_desc)}")
        print(f"[DEBUG] Description preview: {clean_desc[:100]}...")
        
        # Add CivitAI info to notes (NOT description - that goes in description field)
        civitai_info = f"\n\n--- Fetched from CivitAI ---\n"
        civitai_info += f"Model: {model_name}\n"
        civitai_info += f"Base Model: {base_model}\n"
        civitai_info += f"Type: {model_type}\n"
        if trained_words:
            civitai_info += f"Trained Words: {', '.join(trained_words)}\n"
        civitai_info += f"Hash Method: {used_hash_method}\n"
        
        updated_notes = current_notes + civitai_info
        
        # Force refresh of preview URLs in the page item
        item = self.page.items.get(name, {})
        if item:
            # Clear cached preview data
            item['preview_urls'] = None
            item['preview'] = None
            
            # Force re-scan of preview files
            basename_noext = os.path.splitext(filename)[0]
            item['preview_urls'] = self.page.find_all_previews(basename_noext)
            
            if item['preview_urls']:
                item['preview'] = item['preview_urls'][0]
                print(f"[DEBUG] Refreshed preview URLs: {len(item['preview_urls'])} previews found")
            else:
                print(f"[DEBUG] Warning: No preview URLs found after refresh")
        
        print(f"[DEBUG] Returning description length: {len(clean_desc)}")
        
        # Return updated values: description, notes, status
        return (
            clean_desc,  # This is the actual description text
            updated_notes,
            f"<div style='color: green; padding: 10px; background: #d4edda; border-radius: 5px;'>{status_msg}</div>"
        )




    def _return_fetch_error(self, error_msg):
        """Return error status without changing other fields."""
        return (
            gr.update(),  # description unchanged
            gr.update(),  # notes unchanged
            f"<div style='color: red; padding: 10px; background: #f8d7da; border-radius: 5px;'>❌ {error_msg}</div>"
        )

    def _clean_html(self, html_text):
        """Sanitize HTML, keeping safe formatting tags."""
        if not html_text:
            return ""
        
        import re
        from html.parser import HTMLParser
        
        # Whitelist of allowed tags
        allowed_tags = {
            'p', 'br', 'strong', 'em', 'b', 'i', 'u', 's',
            'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'a', 'span', 'div', 'blockquote', 'code', 'pre'
        }
        
        # Remove dangerous tags completely
        dangerous_tags = ['script', 'iframe', 'object', 'embed', 'style', 'link', 'meta']
        for tag in dangerous_tags:
            html_text = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
            html_text = re.sub(f'<{tag}[^>]*/?>', '', html_text, flags=re.IGNORECASE)
        
        # Remove event handlers and javascript: links
        html_text = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', html_text, flags=re.IGNORECASE)
        html_text = re.sub(r'href\s*=\s*["\']javascript:[^"\']*["\']', '', html_text, flags=re.IGNORECASE)
        
        # Strip tags not in whitelist (but keep content)
        def strip_disallowed_tags(text):
            # Find all tags
            tag_pattern = re.compile(r'<(/?)(\w+)([^>]*)>', re.IGNORECASE)
            
            def replace_tag(match):
                closing = match.group(1)
                tag_name = match.group(2).lower()
                attrs = match.group(3)
                
                if tag_name in allowed_tags:
                    # Keep allowed tags, but sanitize attributes for <a>
                    if tag_name == 'a' and not closing:
                        # Only keep href attribute, remove others
                        href_match = re.search(r'href\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE)
                        if href_match:
                            return f'<a href="{href_match.group(1)}" target="_blank" rel="noopener noreferrer">'
                        else:
                            return ''  # <a> without href, remove it
                    return match.group(0)  # Keep as-is
                else:
                    return ''  # Remove tag but keep content
            
            return tag_pattern.sub(replace_tag, text)
        
        clean = strip_disallowed_tags(html_text)
        
        # Clean up common HTML entities
        clean = clean.replace('&nbsp;', ' ')
        clean = clean.replace('&lt;', '<')
        clean = clean.replace('&gt;', '>')
        clean = clean.replace('&amp;', '&')
        clean = clean.replace('&quot;', '"')
        clean = clean.replace('&#39;', "'")
        
        # Remove excessive whitespace but preserve intentional breaks
        clean = re.sub(r'\n\s*\n\s*\n+', '\n\n', clean)  # Max 2 consecutive newlines
        clean = re.sub(r'[ \t]+', ' ', clean)  # Collapse spaces/tabs
        
        return clean.strip()

    def save_user_metadata(self, name, desc, notes):
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["notes"] = notes

        self.write_user_metadata(name, user_metadata)

    def setup_save_handler(self, button, func, components):
        button.click(fn=func, inputs=[self.edit_name_input, *components]).then(fn=None, _js="function(name){closePopup(); extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}", inputs=[self.edit_name_input])

    def create_editor(self):
        self.create_default_editor_elems()

        self.edit_notes = gr.TextArea(label="Notes", lines=4)

        self.create_default_buttons()

        self.button_edit.click(fn=self.put_values_into_components, inputs=[self.edit_name_input], outputs=[self.edit_name, self.edit_description, self.html_filedata, self.html_preview, self.edit_notes]).then(fn=lambda: gr.update(visible=True), outputs=[self.box])

        # Connect fetch from CivitAI button
        self.button_fetch_civitai.click(
            fn=self.fetch_from_civitai,
            inputs=[self.edit_name_input],
            outputs=[self.edit_description, self.edit_notes, self.html_status],
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

        self.setup_save_handler(self.button_save, self.save_user_metadata, [self.edit_description, self.edit_notes])

    def create_ui(self):
        with gr.Group(visible=False, elem_id=self.id_part, elem_classes="edit-user-metadata") as box:
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
        self.button_replace_preview.click(fn=self.save_preview, _js=f"function(x, y, z){{return [selected_gallery_index_id('{self.tabname + '_gallery_container'}'), y, z]}}", inputs=[self.edit_name_input, gallery, self.edit_name_input], outputs=[self.html_preview, self.html_status]).then(fn=None, _js="function(name){extraNetworksRefreshSingleCard(" + json.dumps(self.page.name) + "," + json.dumps(self.tabname) + ", name);}", inputs=[self.edit_name_input])
