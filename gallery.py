import os
import json
from config import log
from database import get_db

async def seed_gallery():
    """
    Scan ./gallery/ and insert any new images into image_pool.
    Auto-tags images based on keywords found in the filename,
    and enriches with metadata from ai_vision_descriptions.json if available.
    """
    if not os.path.isdir("./gallery"):
        log.warning("./gallery/ folder not found — skipping seed.")
        return

    metadata_path = "./gallery/ai_vision_descriptions.json"
    metadata = {}
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as meta_file:
                meta_list = json.load(meta_file)
                for item in meta_list:
                    if item.get("new_filename") and item.get("status") == "success":
                        metadata[item["new_filename"]] = item
            log.info(f"Loaded metadata for {len(metadata)} images.")
        except Exception as e:
            log.error(f"Failed to load gallery metadata: {e}")

    db = await get_db()
    tag_keywords = [
        "coffee", "morning", "nature", "calm", "night", "city",
        "food", "travel", "rain", "books", "aesthetic", "cozy",
        "autumn", "summer", "winter", "spring", "sunset", "ocean",
    ]
    files = [
        f for f in os.listdir("./gallery")
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ]

    for f in files:
        description = f"Gallery image: {f}"
        detected_tags = [t for t in tag_keywords if t in f.lower()]
        
        if f in metadata:
            item = metadata[f]
            if item.get("description"):
                description = item["description"]
            
            slug = item.get("slug")
            if slug and slug != "unknown-slug":
                detected_tags.append(slug.replace("-", " "))
            
            time_of_day = item.get("time_of_day")
            if time_of_day:
                detected_tags.append(f"time:{time_of_day}")

        tags = ",".join(detected_tags) if detected_tags else "general"

        await db.execute(
            """INSERT INTO image_pool (filename, description, tags) 
               VALUES (?, ?, ?)
               ON CONFLICT(filename) DO UPDATE SET 
               description=excluded.description, 
               tags=excluded.tags""",
            (f, description, tags)
        )

    await db.commit()
    log.info(f"🖼️  Gallery seeded — {len(files)} image(s) registered.")


async def select_local_image(tag: str = None) -> dict | None:
    """
    Return a dict containing filename, description, and tags for an image.
    """
    db = await get_db()

    if tag:
        cursor = await db.execute(
            """SELECT filename, description, tags FROM image_pool
               WHERE tags LIKE ?
               ORDER BY use_count ASC, last_sent_at ASC LIMIT 1""",
            (f"%{tag}%",)
        )
    else:
        cursor = await db.execute(
            """SELECT filename, description, tags FROM image_pool
               ORDER BY use_count ASC, last_sent_at ASC LIMIT 1"""
        )

    row = await cursor.fetchone()
    return dict(row) if row else None
