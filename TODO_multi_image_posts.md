# TODO: Multi-Image Bluesky Posts

Bluesky supports up to **4 images per post**. The current `post_to_bluesky` MCP tool only accepts a single `image_path`, so we can only post one image at a time.

## What to change

- Update the tool's input schema to accept a **list of image paths** (with corresponding alt texts) instead of a single path. Keep backward compat by still allowing a single string.
- In `slop_studio/bluesky.py`, build multiple `Image` objects and attach them all to the embed before publishing.
- Validate that no more than 4 images are provided (Bluesky's limit).

## Why

Grid posts look better for batch generations and comparison shots. This is a small change with a big UX payoff.
