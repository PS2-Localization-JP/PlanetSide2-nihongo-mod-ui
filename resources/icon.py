from PIL import Image
import sys


def convert_image_to_ico(image_path, ico_path):
    try:
        # 画像ファイルを読み込む
        with Image.open(image_path) as img:
            # ICOファイルに保存するサイズを指定（例: 16x16, 32x32, 64x64）
            sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            # ICOファイルに保存
            img.save(ico_path, format="ICO", sizes=sizes)
        print(f"Successfully converted {image_path} to {ico_path}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python main.py <input_image> <output_ico>")
    else:
        image_path = sys.argv[1]
        ico_path = sys.argv[2]
        convert_image_to_ico(image_path, ico_path)
