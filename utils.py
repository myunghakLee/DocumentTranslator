from typing import Union, Optional, List, Tuple, Dict, Any
from PIL import Image, ImageDraw, ImageFont, ImageOps
from urllib.parse import unquote_to_bytes
from matplotlib import font_manager as fm
from matplotlib import pyplot as plt
from pathlib import Path
from io import BytesIO

import mimetypes
import base64
import os
import io
import re

try:
    from pydantic import AnyUrl
except Exception:
    from typing import Union as AnyUrl  # pydantic이 없더라도 동작하게


import re
from pylatexenc.latex2text import LatexNodes2Text

# 선택: SymPy 사용 (수식이 복잡하면 실패할 수 있으므로 예외 처리)
try:
    from sympy.parsing.latex import parse_latex
    from sympy import pretty as sympy_pretty
    _HAS_SYMPY = True
except Exception:
    _HAS_SYMPY = False

# 1) 일반 LaTeX 텍스트(본문) -> 유니코드
def plain_latex_to_text(s: str) -> str:
    return LatexNodes2Text().latex_to_text(s)

# 2) 수식(LaTeX) -> 가능한 유니코드 텍스트
def math_latex_to_text(math_src: str) -> str:
    # 우선 간단 치환만으로 충분한 경우
    if not _HAS_SYMPY:
        return LatexNodes2Text().latex_to_text(math_src)
    # SymPy로 시도
    try:
        expr = parse_latex(math_src)
        # 유니코드 예쁘게 출력
        return sympy_pretty(expr, use_unicode=True)
    except Exception:
        # 실패하면 최소 치환
        return LatexNodes2Text().latex_to_text(math_src)

# 3) 전체 문장에서 수식 경계 탐지 후 부분 변환
_MATH_PATTERNS = [
    (re.compile(r"\$\$([\s\S]+?)\$\$", re.MULTILINE), True),   # $$ ... $$
    (re.compile(r"\\\[(.*?)\\\]", re.DOTALL), True),          # \[ ... \]
    (re.compile(r"\$(.+?)\$", re.DOTALL), False),             # $ ... $
    (re.compile(r"\\\((.+?)\\\)", re.DOTALL), False),         # \( ... \)
]

def tex_in_text_to_unicode(text: str) -> str:
    # 우선 본문 내 LaTeX 제어문자를 유니코드로 약하게 정리(수식은 나중에 따로 돌림)
    # 수식을 보호하기 위해 토큰화
    placeholders = []
    def _stash(m):
        placeholders.append(m.group(0))
        return f"__LATEXMATH_{len(placeholders)-1}__"

    # 수식 토큰화
    tmp = text
    for pat, _ in _MATH_PATTERNS:
        tmp = pat.sub(_stash, tmp)

    # 수식 외 본문을 먼저 정리
    tmp = plain_latex_to_text(tmp)

    # 토큰 복원 + 각 수식만 따로 변환
    def _restore(match):
        idx = int(match.group(1))
        raw = placeholders[idx]
        # 경계 제거하고 내용만 추출
        for pat, _ in _MATH_PATTERNS:
            m = pat.match(raw)
            if m:
                inner = m.group(1)
                return math_latex_to_text(inner)
        return raw  # 혹시 모를 안전장치

    tmp = re.sub(r"__LATEXMATH_(\d+)__", _restore, tmp)
    return tmp


class DataUrlHandler:
    """Data URL 처리를 담당하는 클래스"""
    
    @staticmethod
    def _parse_data_url(url_str: str) -> Tuple[str, bool, Optional[str], bytes]:
        """
        data:[<mediatype>][;param=value]*[;base64],<data>
        Returns: (mime, is_base64, name, raw_bytes)
        """
        if not url_str.startswith("data:"):
            raise ValueError("data: URL이 아닙니다.")

        header, data_part = url_str.split(",", 1)
        meta = header[5:]  # strip "data:"

        # 파라미터 분리
        parts = meta.split(";") if meta else []
        # MIME 추출
        mime = parts[0] if parts and "/" in parts[0] else "text/plain"
        params = parts[1:] if parts and "/" in parts[0] else parts

        # base64 여부 / name 파라미터
        is_base64 = any(p.lower() == "base64" for p in params)
        name_param = next((p for p in params if p.lower().startswith("name=")), None)
        name = name_param.split("=", 1)[1] if name_param else None

        # 실제 데이터 디코딩
        if is_base64:
            # 공백/개행/잘못 치환된 공백 보정
            b64 = re.sub(r"\s+", "", data_part).replace(" ", "+")
            raw = base64.b64decode(b64)
        else:
            # 퍼센트 디코딩(UTF-8 텍스트/ SVG 등)
            raw = unquote_to_bytes(data_part)

        return mime, is_base64, name, raw

    @classmethod
    def save_data_url(cls, url: Union[str, Any]) -> Image.Image:
        """
        Pydantic AnyUrl 또는 str의 data URL을 파싱하여 PIL.Image.Image로 반환.
        디스크에는 저장하지 않음.
        """
        s = str(url)  # AnyUrl -> 문자열
        mime, _, name, raw = cls._parse_data_url(s)

        # 이미지 MIME만 허용. (SVG는 벡터이므로 PIL로 바로 열 수 없음)
        if not mime.startswith("image/"):
            raise ValueError(f"image/* data URL이 필요합니다. 현재: {mime}")
        if mime == "image/svg+xml":
            raise ValueError("SVG는 벡터 포맷입니다. 래스터라이즈 후 PIL로 여세요.")

        img = Image.open(BytesIO(raw))
        img.load()  # 버퍼 분리(안전)
        # EXIF 방향 보정(주로 JPEG)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        return img


class FontManager:
    """폰트 로딩 및 캐시 관리 클래스"""
    
    def __init__(self):
        self._font_cache: Dict[Tuple[str, int], ImageFont.ImageFont] = {}
    
    def load_font(self, font_path: str, size: int) -> ImageFont.ImageFont:
        """폰트 로드 헬퍼 함수 (캐시 적용)"""
        cache_key = (font_path, size)
        if cache_key not in self._font_cache:
            try:
                if os.path.exists(font_path):
                    self._font_cache[cache_key] = ImageFont.truetype(font_path, size)
                else:
                    self._font_cache[cache_key] = ImageFont.load_default()
            except:
                self._font_cache[cache_key] = ImageFont.load_default()
        return self._font_cache[cache_key]
    
    def clear_cache(self):
        """폰트 캐시 초기화"""
        self._font_cache.clear()
    
    def get_cache_size(self) -> int:
        """캐시 크기 반환"""
        return len(self._font_cache)
    
    def get_cache_info(self) -> Dict:
        """캐시 정보 반환"""
        return {
            'size': len(self._font_cache),
            'keys': list(self._font_cache.keys())
        }


class TextProcessor:
    """텍스트 처리 및 최적화 클래스"""

    def __init__(self, font_manager: FontManager, font_path: str, 
                 use_xelatex: bool = False, use_encoded_text_for_width_check: bool = True):
        self.font_path = font_path
        self.use_xelatex = use_xelatex
        self.font_manager = font_manager
        self.use_encoded_text_for_width_check = use_encoded_text_for_width_check
        if self.use_xelatex:
            import matplotlib as mpl
            mpl.use("pgf")
            mpl.rcParams.update({
                "pgf.texsystem": "xelatex",          # 또는 "lualatex"
                "pgf.rcfonts": False,                # Matplotlib 폰트 영향 배제
                "pgf.preamble": r"\usepackage{kotex}\usepackage{amsmath}\usepackage{amssymb}\usepackage{mathtools}\usepackage{dsfont}\usepackage{bm}",
            })
        else:
            # 1) 폰트 등록 (세션에 추가)
            fm.fontManager.addfont(font_path)
            self.font_name = fm.FontProperties(fname=font_path).get_name()
            plt.rcParams['font.family'] = self.font_name
            plt.rcParams['axes.unicode_minus'] = False

    def replace_lattext_format(self, text: str) -> str:
        """텍스트에서 Latex 포맷 제거"""
        replace_patterns = [
            (r'\$(.*?)\$', r'\1'),  # $...$ -> ...
            (r'\\\[(.*?)\\\]', r'\1'),  # \[...\] -> ...
            (r'\\\((.*?)\\\)', r'\1'),  # \(...\) -> ...
            (r'\\begin\{.*?\}(.*?)\\end\{.*?\}', r'\1', re.DOTALL),  # \begin{...}...\end{...} -> ...
        ]

        for pattern, repl, *flags in replace_patterns:
            cleaned_text = re.sub(pattern, repl, text, *flags)
        return cleaned_text

    def wrap_text_optimized(self, draw: ImageDraw.Draw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        """최적화된 텍스트 줄바꿈 - 더 스마트한 알고리즘"""
        if not text.strip():
            return []
        
        lines = []
        words = text.split()
        
        if not words:
            return []
        
        # 단일 문자 폭을 미리 계산하여 대략적인 예측 가능
        char_width = draw.textbbox((0, 0), 'M', font=font)[2]  # 'M'은 일반적으로 가장 넓은 문자
        approx_chars_per_line = max(1, max_width // char_width)
        
        current_line = ""
        i = 0
        
        while i < len(words):
            word = words[i]
            
            # 현재 줄이 비어있으면 첫 단어 추가
            if not current_line:
                current_line = word
                i += 1
                continue
            
            # 예상 텍스트 생성
            test_line = f"{current_line} {word}"
            
            # 대략적인 길이 체크로 불필요한 textbbox 호출 줄이기
            if len(test_line) > approx_chars_per_line * 1.5:
                # 정확한 측정
                if self.use_encoded_text_for_width_check:
                    text_width = draw.textbbox((0, 0), tex_in_text_to_unicode(test_line).replace("  ", " "), font=font)[2]
                else:
                    text_width = draw.textbbox((0, 0), test_line, font=font)[2]
                if text_width > max_width:
                    lines.append(current_line)
                    current_line = word
                    i += 1
                    continue
            
            # 실제 폭 측정
            if self.use_encoded_text_for_width_check:
                text_width = draw.textbbox((0, 0), tex_in_text_to_unicode(test_line).replace("  ", " "), font=font)[2]
            else:
                text_width = draw.textbbox((0, 0), test_line, font=font)[2]

            if text_width <= max_width:
                current_line = test_line
                i += 1
            else:
                lines.append(current_line)
                # 단어가 너무 길어서 한 줄에도 안 들어가는 경우 처리
                if draw.textbbox((0, 0), word, font=font)[2] > max_width:
                    # 단어를 문자 단위로 분할
                    char_line = ""
                    for char in word:
                        test_char_line = char_line + char
                        if draw.textbbox((0, 0), test_char_line, font=font)[2] <= max_width:
                            char_line = test_char_line
                        else:
                            if char_line:
                                lines.append(char_line)
                            char_line = char
                    if char_line:
                        current_line = char_line
                    i += 1
                else:
                    current_line = word
                    i += 1
        
        if current_line:
            lines.append(current_line)
        
        return lines

    def find_optimal_font_size_binary(self, draw: ImageDraw.Draw, text: str, font_path: str, 
                                    box_width: int, box_height: int, min_size: int = 1, max_size: int = 45) -> Tuple[int, List[str]]:
        """이진 탐색으로 최적 폰트 크기 찾기 - O(log n) 복잡도"""
        
        def can_fit(font_size):
            """주어진 폰트 크기로 텍스트가 박스에 맞는지 확인"""
            font = self.font_manager.load_font(font_path, font_size)
            lines = self.wrap_text_optimized(draw, text, font, box_width)
            
            line_height = font_size + 2
            total_height = len(lines) * line_height
            
            return total_height <= box_height, lines
        
        # 이진 탐색
        left, right = min_size, max_size
        best_size = min_size
        best_lines = []
        
        while left <= right:
            mid = (left + right) // 2
            fits, lines = can_fit(mid)
            
            if fits:
                best_size = mid
                best_lines = lines
                left = mid + 1  # 더 큰 크기도 시도
            else:
                right = mid - 1  # 더 작은 크기 시도
        
        # 최종 확인
        if not best_lines:
            font = self.font_manager.load_font(font_path, best_size)
            best_lines = self.wrap_text_optimized(draw, text, font, box_width)
        
        return best_size, best_lines

    def fit_text_in_box(self, draw: ImageDraw.Draw, text: str, x1: int, y1: int, x2: int, y2: int, 
                       font_path: str, text_color: Optional[str] = None) -> Optional[Dict]:
        """텍스트를 박스에 맞게 배치 정보 계산 - 그리기는 별도로 수행"""
        
        if not text.strip():
            return None
            
        PADDING = 2  # 여백
        box_width = x2 - x1 - PADDING * 2
        box_height = y2 - y1 - PADDING * 2
        
        if box_width <= 0 or box_height <= 0:
            return None
        
        # 빠른 사전 체크: 최소 폰트로도 안 들어가면 포기
        min_font = self.font_manager.load_font(font_path, 1)
        min_lines = self.wrap_text_optimized(draw, text, min_font, box_width)
        min_height = len(min_lines) * 3  # 최소 line_height
        
        if min_height > box_height:
            # 그래도 최소한 첫 번째 줄이라도 반환
            if min_lines:
                return {
                    'lines': [min_lines[0]],
                    'font': min_font,
                    'font_size': 1,
                    'line_height': 3,
                    'positions': [(x1 + PADDING, y1 + PADDING)],
                    'text_color': text_color or "black"
                }
            return None
        
        # 최적 폰트 크기와 줄바꿈된 텍스트 찾기 (이진 탐색 사용)
        best_font_size, best_lines = self.find_optimal_font_size_binary(
            draw, text, font_path, box_width, box_height
        )
        
        # 텍스트 위치 계산
        font = self.font_manager.load_font(font_path, best_font_size)
        line_height = best_font_size + 2
        start_y = y1 + PADDING
        
        # 수직 중앙 정렬 옵션 (선택사항)
        total_text_height = len(best_lines) * line_height
        if total_text_height < box_height:
            # 여유 공간이 있으면 수직 중앙 정렬
            start_y += (box_height - total_text_height) // 2
        
        # 각 줄의 위치 계산
        positions = []
        lines_to_draw = []
        
        for i, line in enumerate(best_lines):
            y_pos = start_y + i * line_height
            if y_pos + line_height > y2 - PADDING:  # 박스를 벗어나면 중단
                break
            positions.append((x1 + PADDING, y_pos))
            lines_to_draw.append(line)
        
        # print("lines_to_draw: ", lines_to_draw)

        # post processing
        for i in range(len(lines_to_draw) - 1):
            if lines_to_draw[i].count("$") % 2 == 1:
                idx = lines_to_draw[i + 1].find("$")
                lines_to_draw[i] += lines_to_draw[i + 1][:idx+1]
                lines_to_draw[i+1] = lines_to_draw[i+1][idx+1:]

        return {
            'lines': lines_to_draw,
            'font': font,
            'font_size': best_font_size,
            'line_height': line_height,
            'positions': positions,
            'text_color': text_color or "black"
        }

    def insert_textstyle(self, text: str) -> str:
        """
        모든 인라인 수식 $...$의 여는 $ 직후에 \textstyle 을 삽입한다.
        - $$...$$ 블록(디스플레이 수식)은 제외
        - 이미 \textstyle 로 시작하는 수식은 그대로 둠
        """
        INLINE_DOLLAR_PATTERN = re.compile(r'(?<!\\)\$(?!\$)(.*?)(?<!\\)\$(?!\$)', re.DOTALL)
        def _repl(m: re.Match) -> str:
            inner = m.group(1)
            # 들여쓰기/공백 유지 후 \textstyle 삽입
            stripped = inner.lstrip()
            if stripped.startswith(r'\textstyle'):
                return m.group(0)  # 이미 적용됨
            lead_len = len(inner) - len(stripped)
            return '$' + inner[:lead_len] + r'\textstyle ' + stripped + '$'

        return INLINE_DOLLAR_PATTERN.sub(_repl, text)

    def get_plt_text(self, text, fontsize=10):
        plt.figure()
        plt.text(0,1, text, fontsize=fontsize, va='center', ha='left')
        plt.axis('off')
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, transparent=True, bbox_inches='tight', pad_inches=0.0)
        buf.seek(0)
        img = Image.open(buf).convert("RGBA")
        plt.close()
        # img
        return img

    def draw_text_result(self, draw: ImageDraw.Draw, text_result: Dict, page_size: Tuple[int, int]):
        """fit_text_in_box의 결과를 실제로 그리기"""
        image = Image.new('RGBA', page_size)
        # draw = ImageDraw.Draw(image)

        if not text_result:
            return
        
        font = text_result['font']
        text_color = text_result['text_color']
        
        for line, position in zip(text_result['lines'], text_result['positions']):
            # print("position:    ", position, " line:", line)
            if self.use_xelatex:
                pass
                # print("Line : ", line)
                line = self.insert_textstyle(line)
                # print("Line2: ", line)
            try:
                txt_image = self.get_plt_text(line, fontsize=int(font.size*0.60))
                # txt_image.convert("RGB").show()
            except Exception as e:
                print("Warning: plt 그리기 실패, 기본 특수 문자로 시도")
                print("    - Message: ", e)
                line = tex_in_text_to_unicode(line)
                print("Replaced line: ", line)
                txt_image = self.get_plt_text(line, fontsize=int(font.size*0.40))
            # print("line: ", line)

            w, h = txt_image.size


            image.paste(txt_image, (int(position[0]), int(position[1])))
        return image


class DocumentGenerator:
    """문서 생성을 담당하는 클래스"""
    
    def __init__(self, font_path, font_manager: Optional[FontManager] = None, use_xelatex: bool = False, 
                 use_encoded_text_for_width_check: bool = True):
        self.font_path = font_path
        self.font_manager = font_manager or FontManager()
        self.text_processor = TextProcessor(self.font_manager, use_xelatex=use_xelatex, font_path=self.font_path,
                                            use_encoded_text_for_width_check=use_encoded_text_for_width_check)

    def make_text_document(self, text_chunks: List[str], bboxes: List[List], font_path: str, 
                          page_size: Tuple[int, int] = (800, 1200), background_color: str = 'white', 
                          default_text_color: str = 'black', show_boxes: bool = False, 
                          box_color: str = 'red', output_path: Optional[str] = None, 
                          assume_pdf_coords: bool = True) -> Tuple[Image.Image, Dict]:
        """
            여러 텍스트 청크와 bbox를 이용해 한 페이지 문서 생성
        """
        # print("ver1106")

        # 입력 검증
        if len(text_chunks) != len(bboxes):
            raise ValueError(f"텍스트 청크 수({len(text_chunks)})와 bbox 수({len(bboxes)})가 일치하지 않습니다.")
        
        # 페이지 이미지 생성
        image = Image.new('RGB', page_size, color=background_color)
        # image = Image.new('RGBA', page_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        
        # 결과 통계
        success_count = 0
        failed_count = 0
        text_results = []
        
        # # 0단계 전처리
        # print("text_chunks: ", text_chunks)

        # 1단계: 모든 텍스트 정보 계산
        for i, (text, bbox) in enumerate(zip(text_chunks, bboxes)):
            try:
                x1, y1, x2, y2 = bbox
                if assume_pdf_coords:
                    # PDF 좌표계(y 위로 증가) -> 이미지 좌표계(y 아래로 증가) 변환
                    y1, y2 = page_size[1] - y2, page_size[1] - y1

                # bbox 유효성 검사
                if x1 >= x2 or y1 >= y2:
                    print(f"  청크 {i+1}: 잘못된 bbox {bbox} - 건너뜀")
                    failed_count += 1
                    text_results.append(None)
                    continue
                
                # 페이지 경계 확인
                if x1 < 0 or y1 < 0 or x2 > page_size[0] or y2 > page_size[1]:
                    print(f"  청크 {i+1}: bbox {bbox}가 페이지 경계를 벗어남 - 조정")
                    x1 = max(0, x1)
                    y1 = max(0, y1)
                    x2 = min(page_size[0], x2)
                    y2 = min(page_size[1], y2)
                
                # 박스 테두리 그리기 (옵션)
                if show_boxes:
                    draw.rectangle([x1, y1, x2, y2], outline=box_color, width=1)
                
                text_result = self.text_processor.fit_text_in_box(
                    draw, text, x1, y1, x2, y2, self.font_path, default_text_color
                )
                
                if text_result:
                    text_results.append(text_result)
                    success_count += 1
                    # print(f"  청크 {i+1}: 성공 - 폰트크기 {text_result['font_size']}, {len(text_result['lines'])}줄")
                else:
                    text_results.append(None)
                    failed_count += 1
                    print(f"  청크 {i+1}: 실패 - 텍스트가 박스에 맞지 않음")
                    
            except Exception as e:
                text_results.append(None)
                failed_count += 1
                print(f"  청크 {i+1}: 오류 - {e}")
        
        # 2단계: 성공한 텍스트들을 실제로 그리기
        drawn_count = 0
        for i, text_result in enumerate(text_results):
            if text_result:
                try:
                    # print(f"  그리기 청크 {i+1}: {text_result['lines'][:1]}... 색상={text_result['text_color']}")
                    txt_image = self.text_processor.draw_text_result(draw, text_result, page_size)
                    # txt_image.show()
                    

                    pos = text_result['positions']
                    # print(txt_image.size, image.size, pos)
                    image.paste(txt_image, (0, 0), mask=txt_image)
                    # image.show()

                    drawn_count += 1
                except Exception as e:
                    print(f"  그리기 오류 청크 {i+1}: {e}")
                    failed_count += 1
                    success_count -= 1
        # image.show()
        # 결과 요약
        result_info = {
            'success_count': success_count,
            'failed_count': failed_count,
            'drawn_count': drawn_count,
            'total_count': len(text_chunks),
            'results': text_results,
            'page_size': page_size
        }
        
        # print(f"문서 생성 완료: 성공 {success_count}개, 실패 {failed_count}개, 그리기 완료 {drawn_count}개")
        
        # 파일 저장 (옵션)
        if output_path:
            image.save(output_path)
            # print(f"문서가 '{output_path}'에 저장되었습니다.")
        
        return image, result_info

    def make_figure_document(self, figure, bboxes: List[List], 
                           page_size: Tuple[int, int] = (800, 1200), 
                           assume_pdf_coords: bool = True) -> Tuple[Image.Image, Dict]:
        """
        여러 그림(이미지) 청크와 bbox로 한 페이지 문서 생성.
        - 리사이즈(스케일) 없음: 원본 크기 그대로 붙임
        - 알파 합성: RGBA 마스크 사용
        - PDF 좌표계 옵션: y축 반전
        """
        # if len(figures) != len(bboxes):
        #     raise ValueError(f"그림 수({len(figures)})와 bbox 수({len(bboxes)})가 일치하지 않습니다.")

        W, H = page_size
        if type(figure) == dict:
            figure = DataUrlHandler.save_data_url(figure['uri'])
            figure = ImageOps.exif_transpose(figure)
        # RGBA 캔버스 만들고 마지막에 RGB로 변환
        base = Image.new("RGBA", page_size, color=(0, 0, 0, 0))
        
        success_count, failed_count = 0, 0
        results = []
        # figures = [DataUrlHandler.save_data_url(fig) for fig in figures] 
        
        for i, bbox in enumerate(bboxes):
            try:
                # bbox 정규화
                x1, y1, x2, y2 = bbox
                if x1 > x2: x1, x2 = x2, x1
                if y1 > y2: y1, y2 = y2, y1

                # PDF 좌표계면 y 반전
                if assume_pdf_coords:
                    y1, y2 = H - y2, H - y1
                crop = figure.crop((x1, y1, x2, y2))


                # # 원본 크기 그대로 사용 (스케일 제거)
                if crop.mode != "RGBA":
                    crop = crop.convert("RGBA")
                fw, fh = crop.size

                # # bbox 중앙 정렬 위치 (넘치면 그대로 넘어감)
                bw, bh = int(x2 - x1), int(y2 - y1)
                paste_x = int(x1 + (bw - fw) / 2)
                paste_y = int(y1 + (bh - fh) / 2)

                # 알파 마스크로 합성
                base.paste(crop, (paste_x, paste_y), crop)

                results.append({
                    "bbox": (x1, y1, x2, y2),
                    "final_size": (fw, fh),
                    "position": (paste_x, paste_y)
                })
                success_count += 1

            except Exception as e:
                failed_count += 1
                print("Error processing figure:", e)
                results.append({"bbox": bbox, "error": str(e)})

        result_info = {
            "success_count": success_count,
            "failed_count": failed_count,
            "total_count": len(bboxes),
            "results": results,
        }

        return base.convert("RGBA"), result_info


# 하위 호환성을 위한 전역 인스턴스들
# _global_font_manager = FontManager()
# _global_text_processor = TextProcessor(_global_font_manager)
# _global_document_generator = DocumentGenerator(_global_font_manager)

# 하위 호환성을 위한 함수들 (기존 코드와의 호환성 유지)
# def _load_font(font_path, size):
#     return _global_font_manager.load_font(font_path, size)

# def _wrap_text_optimized(draw, text, font, max_width):
#     return _global_text_processor.wrap_text_optimized(draw, text, font, max_width)

# def _find_optimal_font_size_binary(draw, text, font_path, box_width, box_height, min_size=1, max_size=60):
#     return _global_text_processor.find_optimal_font_size_binary(draw, text, font_path, box_width, box_height, min_size, max_size)

# def _fit_text_in_box(draw, text, x1, y1, x2, y2, font_path, text_color=None):
#     return _global_text_processor.fit_text_in_box(draw, text, x1, y1, x2, y2, font_path, text_color)

# def draw_text_result(draw, text_result):
#     return TextProcessor.draw_text_result(draw, text_result)

# def make_text_document(text_chunks, bboxes, font_path, page_size=(800, 1200), background_color='white', 
#                       default_text_color='black', show_boxes=False, box_color='red', output_path=None, 
#                       assume_pdf_coords=True):
#     return _global_document_generator.make_text_document(text_chunks, bboxes, font_path, page_size, 
#                                                         background_color, default_text_color, show_boxes, 
#                                                         box_color, output_path, assume_pdf_coords)

# def make_figure_document(figures, bboxes, page_size=(800, 1200), assume_pdf_coords=True):
#     return _global_document_generator.make_figure_document(figures, bboxes, page_size, assume_pdf_coords)

# def save_data_url(url):
#     return DataUrlHandler.save_data_url(url)


# if __name__ == "__main__":
#     font = "./IBMPlexSansKR-Light.ttf"
    
#     # 클래스 기반 테스트
#     print("=== 클래스 기반 테스트 ===")
    
#     # FontManager 테스트
#     font_manager = FontManager()
#     print(f"초기 캐시 크기: {font_manager.get_cache_size()}")
    
#     font1 = font_manager.load_font(font, 12)
#     font2 = font_manager.load_font(font, 16)
#     print(f"폰트 로드 후 캐시 크기: {font_manager.get_cache_size()}")
#     print(f"캐시 정보: {font_manager.get_cache_info()}")
    
#     # DocumentGenerator 테스트
#     doc_gen = DocumentGenerator(font_manager)
    
#     bboxes = [
#         [100, 100, 400, 300],
#         [500, 300, 700, 800],
#     ]
#     text = [
#         "Softmax ( o S / τ ) is",
#         "두번째 테스트 (클래스 버전)"
#     ]
    
#     print("\n=== 클래스 기반 문서 생성 ===")
#     image, info = doc_gen.make_text_document(
#         text, bboxes, font, 
#         output_path="document_class_based.png", 
#         background_color='lightblue', 
#         default_text_color='darkblue', 
#         show_boxes=True
#     )
#     print(f"결과: {info}")
    
#     # 하위 호환성 테스트
#     print("\n=== 하위 호환성 테스트 ===")
#     image2, info2 = make_text_document(
#         text, bboxes, font, 
#         output_path="document_backward_compatible.png", 
#         background_color='lightgreen', 
#         default_text_color='darkgreen', 
#         show_boxes=True
#     )
#     print(f"결과: {info2}")
#     image2.save("document_backward_compatible.png")
#     print("\n모든 테스트 완료!")



def test_special_characters(font_path, test_texts=[]):
    """특수문자 렌더링 테스트"""
    
    font_path = font_path
    
    # 특수문자가 포함된 텍스트들
    if len(test_texts) == 0:
        test_texts = [
            "Softmax ( o S / τ )",  # 그리스 문자 타우
            "Temperature: 25℃",     # 섭씨 기호
            "Math: α + β = γ",      # 그리스 문자들
            "Symbols: ※◆★♥♠",     # 특수 기호들
            "Numbers: ①②③④⑤",     # 원문자
            "안녕하세요 Hello",      # 한글 + 영어
        ]
    
    # bbox 설정
    bboxes = []
    for i in range(len(test_texts)):
        y_start = 50 + i * 100
        bboxes.append([50, y_start, 750, y_start + 80])
    
    print("=== 특수문자 렌더링 테스트 ===")
    
    # DocumentGenerator로 테스트
    doc_gen = DocumentGenerator()
    
    image, info = doc_gen.make_text_document(
        test_texts, 
        bboxes, 
        font_path,
        page_size=(800, 700),
        background_color='white',
        default_text_color='black',
        show_boxes=True,
        box_color='red',
        output_path="test_special_characters.png"
    )
    
    print(f"결과: {info}")
    print("test_special_characters.png 저장됨")
    
    # 각 텍스트별 폰트 지원 확인
    font_manager = FontManager()
    print("\n=== 폰트 지원 확인 ===")
    for text in test_texts:
        # 기존 방식
        basic_support = font_manager.check_font_supports_text(font_path, text)
        
        # 새로운 방식으로 실제 폰트 로드
        font = font_manager.load_font_with_fallback(font_path, 16, text)
        
        print(f"'{text}': 기본={basic_support}")
