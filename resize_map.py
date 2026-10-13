#!/usr/bin/env python3
"""Resize office map by SHORT EDGE scaling (keep aspect ratio, no stretching/cropping)"""

from PIL import Image

def resize_map(input_path, output_path, target_short_edge=600):
    im = Image.open(input_path)
    original_width, original_height = im.size
    
    # Determine which is the SHORT edge
    if original_width < original_height:
        short_edge, long_edge = original_width, original_height
        is_width_short = True
    else:
        short_edge, long_edge = original_height, original_width
        is_width_short = False
    
    # Calculate scale based on SHORT edge
    scale = target_short_edge / short_edge
    
    # Compute new dimensions
    if is_width_short:
        new_width = target_short_edge
        new_height = int(long_edge * scale)
    else:
        new_width = int(long_edge * scale)
        new_height = target_short_edge
    
    # Resize (use LANCZOS for high quality)
    im_resized = im.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    im_resized.save(output_path)
    print(f"Resized map saved: {output_path}")
    print(f"Original size: {original_width}x{original_height}")
    print(f"Resized size: {new_width}x{new_height}")
    print(f"Short edge scale: {scale:.2f}x")

if __name__ == "__main__":
    # SOU-46: the one-shot hardcoded input is gone; pass paths explicitly.
    import os
    import sys

    repo = os.path.dirname(os.path.abspath(__file__))
    default_in = os.path.join(repo, "assets", "room-reference.png")
    default_out = os.path.join(repo, "frontend", "office_bg.png")
    input_path = sys.argv[1] if len(sys.argv) > 1 else default_in
    output_path = sys.argv[2] if len(sys.argv) > 2 else default_out
    resize_map(input_path, output_path, target_short_edge=720)
