import os
import json
import glob
from datetime import datetime
from modules import scripts, shared, sd_samplers
from modules.processing import StableDiffusionProcessingTxt2Img, process_images, Processed
from modules import images as images_module

class Script(scripts.Script):
    def title(self):
        return "Generate from Consolidated JSON Metadata"

    def show(self, is_img2img):
        return not is_img2img

    def ui(self, is_img2img):
        import gradio as gr
        
        use_random_seed = gr.Checkbox(label='Use random seed (ignore seed from JSON)', value=False)
        batch_count = gr.Number(label='Batch count (how many times to run through entire JSON)', value=1, minimum=1, maximum=100, step=1, precision=0)
        run_adetailer = gr.Checkbox(label='Run ADetailer', value=True)
        
        return [use_random_seed, batch_count, run_adetailer]

    def run(self, p, use_random_seed, batch_count, run_adetailer, *args):
        import warnings
        import logging
        logging.getLogger('ControlNet').setLevel(logging.ERROR)
        warnings.filterwarnings('ignore', message='.*Sampler Scheduler autocorrection.*')
        
        batch_count = int(batch_count)
        print(f"Settings: Use random seed = {use_random_seed}, Batch count = {batch_count}, Run ADetailer = {run_adetailer}")
        
        base_path = shared.data_path if shared.data_path is not None else os.getcwd()
        print(f"Base path: {base_path}")

        json_folder = os.path.join(base_path, "scripts", "json_to_txt2img")
        print(f"Looking for JSON files in: {json_folder}")
        
        if not os.path.exists(json_folder):
            print(f"Error: Folder '{json_folder}' not found")
            return
        
        json_pattern = os.path.join(json_folder, "prompt_data*.json")
        json_files = glob.glob(json_pattern)
        
        if not json_files:
            print(f"Error: No 'prompt_data*.json' files found in '{json_folder}'")
            return
        
        print(f"Found {len(json_files)} JSON file(s) to process:")
        for jf in json_files:
            print(f"  - {os.path.basename(jf)}")

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

        date_folder = datetime.now().strftime("%Y-%m-%d")
        total_images_all_files = 0
        
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
            else:
                suffix = json_basename.replace("prompt_data_", "").replace(".json", "")
                outdir = os.path.join(default_base_outdir, date_folder, suffix)
            
            print(f"Output directory: {outdir}")
            os.makedirs(outdir, exist_ok=True)

            valid_entries = [entry for entry in file_data.items() if entry[1] and ('prompt' in entry[1] or 'parameters' in entry[1])]
            
            # Filter out entries marked with skip=true
            original_count = len(valid_entries)
            valid_entries = [(k, v) for k, v in valid_entries if not v.get('skip', False)]
            skipped_count = original_count - len(valid_entries)
            
            if skipped_count > 0:
                print(f"Skipping {skipped_count} entries marked with skip=true")
            
            total_images = len(valid_entries) * batch_count
            print(f"Total images to generate: {len(valid_entries)} entries × {batch_count} batch(es) = {total_images}")

            if len(valid_entries) == 0:
                print("No valid entries found, skipping...")
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
                    shared.state.textinfo = f"[{os.path.basename(json_file)}] Batch {batch_num + 1}/{batch_count} - Job {idx + 1}/{len(valid_entries)}: {image_key}"
                    print(f"\nBatch {batch_num + 1}/{batch_count} - Job {idx + 1}/{len(valid_entries)} for {image_key}")
                    print(f"DEBUG: idx={idx}, batch={batch_num}, overall_idx={overall_idx}")

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

                    # Get the actual seed used after generation
                    actual_seed = result.all_seeds[0] if result.all_seeds else proc.seed
                    
                    # Check if this entry has an image_name field (from filtered JSON)
                    has_image_name = 'image_name' in metadata and metadata['image_name']
                    
                    if has_image_name:
                        # Remove extension from original image name (if it has one)
                        original_name = os.path.splitext(metadata['image_name'])[0]
                        
                        # Build filename: {original_name}-{seed}-b{batch}
                        if batch_count > 1:
                            final_filename = f"{original_name}-{actual_seed}-b{batch_num}"
                        else:
                            final_filename = f"{original_name}-{actual_seed}"
                        
                        print(f"DEBUG: Using image_name: {metadata['image_name']}")
                        print(f"DEBUG: Final filename: {final_filename}.png")
                        
                        # Save directly without A1111 sequence prefix
                        from PIL import PngImagePlugin
                        
                        for i, image in enumerate(result.images):
                            full_path = os.path.join(outdir, f"{final_filename}.png")
                            
                            # Create PNG info object with metadata
                            pnginfo = PngImagePlugin.PngInfo()
                            pnginfo.add_text("parameters", result.info)
                            
                            # Save with metadata embedded
                            image.save(full_path, pnginfo=pnginfo)
                            
                            print(f"Saved directly: {final_filename}.png (no seq prefix)")
                    else:
                        # Build the filename suffix with seed, index, and batch
                        if batch_count > 1:
                            filename_suffix = f"{actual_seed}-{idx}-b{batch_num}"
                        else:
                            filename_suffix = f"{actual_seed}-{idx}"
                        
                        print(f"DEBUG: No image_name field")
                        print(f"DEBUG: Actual seed used: {actual_seed}")
                        print(f"DEBUG: Filename suffix: {filename_suffix}")
                        
                        # Set the pattern so A1111 adds sequence number automatically
                        shared.opts.samples_filename_pattern = filename_suffix
                        
                        for i, image in enumerate(result.images):
                            images_module.save_image(
                                image,
                                path=outdir,
                                basename="",
                                seed=actual_seed,
                                prompt=proc.prompt,
                                extension=shared.opts.samples_format,
                                info=result.info,
                                p=proc
                            )
                            print(f"Saved with A1111: [seq]-{filename_suffix}")

                    shared.state.sampling_step = 0
                    shared.state.job_no = overall_idx + 1

                print(f"\nBatch {batch_num + 1}/{batch_count} complete")
            
            shared.opts.save_to_dirs = original_save_to_dirs
            shared.opts.samples_filename_pattern = original_samples_filename_pattern

        shared.state.textinfo = f"Complete: {total_images_all_files} total images"
        print(f"\n{'='*60}")
        print(f"ALL DONE - {total_images_all_files} images generated!")
        print(f"{'='*60}")
        return result if 'result' in locals() else None

if __name__ == "__main__":
    print("Run within AUTOMATIC1111 WebUI")