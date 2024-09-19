import os
from PIL import Image
import imagehash
from collections import defaultdict

def remove_duplicate_images(folder_path, screenshots, hash_size=16, similarity_threshold=5):
    unique_screenshots = []
    hash_dict = defaultdict(list)

    for screenshot in screenshots:
        img_path = os.path.join(folder_path, screenshot['filename'])
        try:
            with Image.open(img_path) as img:
                # Use phash instead of average_hash for better accuracy
                img_hash = imagehash.phash(img, hash_size=hash_size)

            is_duplicate = False
            for existing_hash in hash_dict:
                if img_hash - existing_hash <= similarity_threshold:
                    is_duplicate = True
                    os.remove(img_path)
                    print(f"Removed duplicate image: {screenshot['filename']}")
                    break

            if not is_duplicate:
                hash_dict[img_hash].append(screenshot)
                unique_screenshots.append(screenshot)

        except IOError as e:
            print(f"Error processing {screenshot['filename']}: {e}")
        except Exception as e:
            print(f"Unexpected error with {screenshot['filename']}: {e}")

    return unique_screenshots

# Example usage:
# unique_screenshots = remove_duplicate_images('/path/to/folder', screenshots, hash_size=16, similarity_threshold=10)