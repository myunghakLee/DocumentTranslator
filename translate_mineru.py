from pdf2image import convert_from_path
from glob import glob
import argparse
import shutil
import json
import time
import os

from matplotlib import pyplot as plt
import matplotlib.pyplot as plt
import matplotlib as mpl

from PIL import Image, ImageOps
from PIL import ImageOps
from PIL import Image

from utils import DocumentGenerator
from translator import Translator
from parser import mineru_parser

def make_md(content_list):
    text = ""

    for content in content_list:  # page 단위
        if content["type"] == "image":
            text += f"![]({content['img_path']})\n"
            text += " ".join(content.get("image_caption", [])) + "\n"
            text += " ".join(content.get("image_footnote", []))

        elif content["type"] == "equation" or content["type"] == "table":
            text += f"![]({content['img_path']})\n"

        elif content["type"] == "table":
            text += f"![]({content['img_path']})\n"
            text += " ".join(content.get("table_caption", [])) + "\n"
            text += " ".join(content.get("table_footnote", []))

            text += "\n\n"
        elif content["type"] == "text":
            if content.get("text_level", -1) == 1:
                text += "# "
            text += content["text"] + "\n"

        text += "\n\n"

    return text

def png_folder_to_pdf(folder: str, output_pdf: str = "out.pdf") -> int:
    files = sorted(glob(f"{folder}/*.png"))
    images = []
    for p in files:
        im = Image.open(p)
        # PDF는 RGB 권장. 알파/팔레트 안전 변환
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode == "P":
            im = im.convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        images.append(im)

    first, rest = images[0], images[1:]
    print("Saving PDF to:", output_pdf)
    first.save(output_pdf, save_all=True, append_images=rest)
    return len(images)

class PDFProcessor:
    def __init__(self, pdf_path: str, output_dir: str,
                 API_KEY: str, source_language: str, target_language: str, font_path: str,
                 dpi: int = 300, use_xelatex=True, save_parsing_results: bool = False,
                 save_origin_image: bool = False, save_md: bool = False,
                 copy2image: set = set(['image_body', 'table_body', 'title', 
                                        'discarded', 'interline_equation'])):
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.copy2image = copy2image

        os.environ["GEMINI_API_KEY"] = API_KEY
        self.source = source_language
        self.target = target_language
        self.translator = Translator()

        self.pages = convert_from_path(self.pdf_path, dpi=dpi)
        self.save_json = save_parsing_results
        self.save_origin_image = save_origin_image
        self.save_md = save_md
        self.use_xelatex = use_xelatex

        if self.use_xelatex:
            mpl.use("pgf")
            mpl.rcParams.update({
                "pgf.texsystem": "xelatex",          # 또는 "lualatex"
                "pgf.rcfonts": False,                # Matplotlib 폰트 영향 배제
                "pgf.preamble": r"\usepackage{kotex}\usepackage{amsmath}\usepackage{amssymb}\usepackage{mathtools}\usepackage{dsfont}\usepackage{bm}",
            })
        else:
            from matplotlib import font_manager as fm
            font_path = "./GmarketSansTTFLight.ttf"  # 한글 폰트 파일 경로
            fm.fontManager.addfont(font_path)
            font_name = fm.FontProperties(fname=font_path).get_name()
            plt.rcParams['font.family'] = font_name
            plt.rcParams['axes.unicode_minus'] = False

        self.DG = DocumentGenerator(font_path=font_path, use_xelatex=self.use_xelatex)

    def pdf_parsing(self) -> dict:
        # base_name = os.path.basename(self.pdf_path).replace(".pdf", "")

        result_data, output_dir = mineru_parser.mineru_parser(self.pdf_path)
        if self.save_json:
            with open(f"{self.output_dir}/parsing_result.json", "w") as f:
                json.dump(result_data, f, ensure_ascii=False, indent=4)

        return result_data

    def split_text_by_page(self, total_texts, total_txt_labels, total_txt_boxes) -> list:

        def get_area(box):
            xmin, ymin, xmax, ymax = box
            return (xmax - xmin) * (ymax - ymin)

        def get_first_text_idx(texts, labels, target_label='text'):
            for idx in range(len(texts)):
                if labels[idx] == target_label:
                    return idx
            return -1

        def get_last_text_idx(texts, labels, target_label='text'):
            for idx in range(len(texts)-1, -1, -1):
                if labels[idx] == target_label:
                    return idx
            return -1

        split_mark = '. '
        for idx in range(1, len(total_texts)):
            text_idx = get_first_text_idx(total_texts[idx], total_txt_labels[idx])
            if text_idx == -1:
                continue
            if len(total_texts[idx][text_idx].replace(" ", "").strip()) == 0:
                text_idx_before = get_last_text_idx(total_texts[idx-1], total_txt_labels[idx-1])
                before_area = get_area(total_txt_boxes[idx-1][text_idx_before])
                cur_area = get_area(total_txt_boxes[idx][text_idx])
                before_text = total_texts[idx-1][text_idx_before]
                before_sent = before_text.split(split_mark)
                
                idx_sent = len(before_sent)
                before_text = "".join(before_sent[:idx_sent])
                cur_text = "".join(before_sent[idx_sent:])

                while (before_area / cur_area < len(before_text) / (len(cur_text) + 1)):
                    idx_sent -= 1
                    if idx_sent < 0:
                        break
                    before_text = "".join(before_sent[:idx_sent]) + split_mark
                    cur_text = "".join(before_sent[idx_sent:])
                    
                total_texts[idx][text_idx] = cur_text
                total_texts[idx-1][text_idx_before] = before_text

        return total_texts

    def post_processing(self, data):
        self.sizes = [d.size for d in self.pages]
        total_txt_boxes = []
        total_txt_labels = []
        total_texts = []
        total_img_boxes = []
        total_imgs = []
        total_order = []

        copy2image = set(['image_body', 'table_body', 'title', 'discarded', 'interline_equation'])

        for idx in range(len(self.sizes)):
            W, H = self.sizes[idx]
            img_labels = []
            txt_labels = []
            txt_boxes = []
            img_boxes = []
            texts = []
            imgs = []
            order = []
            
            for element in data[idx]:
                xmin, ymin, xmax, ymax = element['bbox']
                xmin, xmax = xmin * W, xmax * W
                ymin, ymax = ymin * H, ymax * H
                if element['type'] in copy2image:
                    img_labels.append(element['type'])
                    imgs.append(self.pages[idx].crop((xmin, ymin, xmax, ymax)))
                    img_boxes.append([xmin, ymin, xmax, ymax])
                    order.append('image')
                else:
                    txt_labels.append(element['type'])
                    texts.append(element['text'])
                    txt_boxes.append([xmin, ymin, xmax, ymax])
                    order.append('text')
                
            total_txt_boxes.append(txt_boxes)
            total_txt_labels.append(txt_labels)
            total_texts.append(texts)
            total_img_boxes.append(img_boxes)
            total_imgs.append(imgs)
            total_order.append(order)

        return total_texts, total_txt_labels, total_txt_boxes, total_img_boxes, total_imgs, total_order

    def concat_h(self, img_left: Image.Image, img_right: Image.Image,
                gap: int = 0, bg=None, target_height: int | str = "max") -> Image.Image:
        # 1) EXIF 방향 보정
        img_left  = ImageOps.exif_transpose(img_left)
        img_right = ImageOps.exif_transpose(img_right)

        # 2) 모드 통일 (투명도가 하나라도 있으면 RGBA)
        has_alpha = ("A" in img_left.getbands()) or ("A" in img_right.getbands())
        mode = "RGBA" if has_alpha else "RGB"
        img_left  = img_left.convert(mode)
        img_right = img_right.convert(mode)

        # 3) 목표 높이 결정: "max"=큰 쪽 기준, "min"=작은 쪽 기준, 또는 정수 지정
        h1, h2 = img_left.height, img_right.height
        if isinstance(target_height, int):
            H = target_height
        elif target_height == "min":
            H = min(h1, h2)
        else:  # "max" (default)
            H = max(h1, h2)

        # 4) 비율 유지하며 높이 H로 리사이즈
        def resize_to_h(im, H):
            W = int(round(im.width * H / im.height))
            return im.resize((W, H), Image.LANCZOS)
        L = resize_to_h(img_left,  H)
        R = resize_to_h(img_right, H)

        # 5) 캔버스 생성 (배경색 기본: 투명 또는 흰색)
        if bg is None:
            bg = (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255)
        canvas = Image.new(mode, (L.width + gap + R.width, H), bg)

        # 6) 붙이기
        canvas.paste(L, (0, 0))
        canvas.paste(R, (L.width + gap, 0))
        return canvas

    def forward(self, attempt_limit=5) -> Image.Image:
        """
            번역된 텍스트를 LaTeX로 렌더링하여 이미지로 반환합니다.
        """
        print("Start PDF Processing...")
        result_data = self.pdf_parsing()
        print("Post Processing...")
        total_texts, total_txt_labels, total_txt_boxes, total_img_boxes, total_imgs, total_order = self.post_processing(result_data)
        total_texts = self.split_text_by_page(
            total_texts, total_txt_labels, total_txt_boxes
        )
        os.makedirs(f"{output_dir}/imgs", exist_ok=True)


        kor_md_data = ""
        print("Translation & Image Generation...")
        for idx in range(len(total_texts)):  # 페이지 단위

            texts = total_texts[idx]
            origin_image = self.pages[idx]
            if len(texts) < 1: # 오류난 경우
                image_text = Image.new('RGB', (self.sizes[idx][0], self.sizes[idx][1]), color='white')

            else:
                bboxes = total_txt_boxes[idx]
                width = self.sizes[idx][0]
                height = self.sizes[idx][1]

                eng_text = '\"\n-----\n\"'.join(total_texts[idx])
                for _att in range(attempt_limit):
                    try:
                        trans_text = self.translator.translate(eng_text, self.source, self.target)
                        trans_text_split = trans_text.split('\n-----\n')
                        if len(trans_text_split) != len(total_txt_boxes[idx]):
                            trans_text_split = trans_text.replace('\n-----\n', '\n').split('\n')

                        if len(trans_text_split) == len(bboxes):
                            break

                        if _att == attempt_limit - 1:
                            print("Warning: Translation attempt limit reached. Proceeding with mismatched lengths.")
                            print("Original text count:")
                            for d in total_texts:
                                print("    - ", d)
                            print("Translated text count:")
                            for d in trans_text_split:
                                print("    - ", d)
                            break
                        
                        time.sleep(1)  # 잠시 대기 후 재시도
                    except:
                        print("Warning: Error during translation. Retrying...")
                        continue
                
                # for eng_text, kor_text in zip(total_texts[idx], trans_text_split):
                #     kor_md_data = kor_md_data.replace(eng_text, kor_text)



                image_text, _ = self.DG.make_text_document(text_chunks = trans_text_split, bboxes = bboxes, font_path="GmarketSansTTFLight.ttf", page_size = (width, height), assume_pdf_coords=False)
                image_fig, _ = self.DG.make_figure_document(self.pages[idx], total_img_boxes[idx], page_size = (width, height), assume_pdf_coords=False)
                image_text.paste(image_fig, (0,0), mask=image_fig)

                

            if self.save_md:
                os.makedirs(f"{output_dir}/tmp/", exist_ok=True)
                text_idx = 0
                img_idx = 0
                order = total_order[idx]
                for o in order:
                    if o == 'text':
                        kor_md_data += f"{trans_text_split[text_idx]}\n\n"
                        text_idx += 1
                    elif o == 'image':
                        total_imgs[idx][img_idx].save(f"{output_dir}/tmp/{str(idx).zfill(4)}_{str(img_idx).zfill(4)}.png")
                        kor_md_data += f"![Image {img_idx}](tmp/{str(idx).zfill(4)}_{str(img_idx).zfill(4)}.png)\n\n"
                        img_idx += 1

            image_text.save(f"{output_dir}/imgs/_translated_{str(idx).zfill(5)}.png")
            if self.save_origin_image:
                self.concat_h(origin_image, image_text).save(f"{output_dir}/imgs/concat_{str(idx).zfill(5)}.png")

        if self.save_md:
            with open(f"{output_dir}/translated_full_{self.target}.md", "w", encoding="utf-8") as f:
                f.write(kor_md_data)

if __name__ == "__main__":
    # args settings
    parser = argparse.ArgumentParser(description="Process some PDFs.")
    parser.add_argument("--source_language", default="English", help="Source language")
    parser.add_argument("--target_language", default="Korean", help="Target language")
    parser.add_argument("--pdf_path", required=True, help="Path to the PDF file")
    parser.add_argument("--output_dir", default="./outputs/", help="Directory to save outputs")
    parser.add_argument("--font_path", default="./fonts/GmarketSansTTFLight.ttf", help="Path to the font file")
    parser.add_argument("--save_origin_image", action="store_true", help="Whether to save original images")
    parser.add_argument("--save_md", action="store_true", help="Whether to save markdown files")
    parser.add_argument("--use_mathtex", action="store_true", help="Whether to don't use XeLaTeX for rendering")
    parser.add_argument("--save_parsing_results", action="store_true", help="Whether to save parsing results as JSON")
    args = parser.parse_args()

    start_time = time.time()
    pdf_path = args.pdf_path
    base_name = os.path.basename(pdf_path).replace(".pdf", "")
    output_dir = f"./{args.output_dir}/{base_name}/"
    os.makedirs(output_dir, exist_ok=True)

    API_KEY = open("API_KEY.txt", "r").read().strip()
    if len(API_KEY.replace(" ", "")) == 0:
        raise ValueError("Enter your Gemini API key in the 'API_KEY.txt' file")
    print("Using API Key:", API_KEY)

    source_language = "English"
    target_language = "Korean"
    use_xelatex = not args.use_mathtex

    processor = PDFProcessor(pdf_path, output_dir,
                             API_KEY, source_language, target_language,
                             font_path=args.font_path, save_origin_image=args.save_origin_image,
                             save_parsing_results=args.save_parsing_results, save_md=args.save_md,
                             use_xelatex=use_xelatex)
    processor.forward()

    png_folder_to_pdf(f"{output_dir}/imgs/", output_pdf=f"{output_dir}/translated_{base_name}.pdf")

    end_time = time.time()
    print(f"Total processing time: {end_time - start_time:.2f} seconds")