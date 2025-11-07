from html.parser import HTMLParser
import html as html_mod

class TableTextExtractor(HTMLParser):
    def __init__(self, sep_row="\n", sep_cell=" "):
        super().__init__(convert_charrefs=False)  # 수동으로 html.unescape 처리
        self.sep_row = sep_row
        self.sep_cell = sep_cell
        self.reset_state()

    def reset_state(self):
        """상태 초기화 - 재사용을 위해"""
        self.out = []
        self._in_cell = False
        self._row_started = False
        self._cell_started = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            # 새로운 행 시작: 이전 행이 있었다면 줄바꿈 추가
            if self._row_started:
                self.out.append(self.sep_row)
            self._row_started = True
            self._cell_started = False
        elif tag in ("td", "th"):
            # 새로운 셀 시작: 같은 행에서 첫 셀이 아니면 셀 구분자 추가
            if self._cell_started:
                self.out.append(self.sep_cell)
            self._in_cell = True
            self._cell_started = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False

    def handle_data(self, data):
        if not data:
            return
        if self._in_cell:
            # 공백 정리 후 내용 추가
            text = " ".join(data.split())
            if text:
                self.out.append(html_mod.unescape(text))

    def handle_entityref(self, name):
        # &nbsp; 같은 엔티티도 데이터처럼 처리
        if self._in_cell:
            self.out.append(html_mod.unescape(f"&{name};"))

    def handle_charref(self, name):
        if self._in_cell:
            self.out.append(html_mod.unescape(f"&#{name};"))

    def table_html_to_text(self, html: str, sep_row: str = None, sep_cell: str = None) -> str:
        """
        <table> 기반 마크업에서 모든 태그/속성을 제거하고 셀 텍스트만 반환.
        - html: 처리할 HTML 문자열
        - sep_row: 행 구분자 (기본값: 인스턴스 설정값)
        - sep_cell: 셀 구분자 (기본값: 인스턴스 설정값)
        """
        # 상태 초기화
        self.reset_state()
        
        # 임시로 구분자 변경 (인자가 주어진 경우)
        original_sep_row = self.sep_row
        original_sep_cell = self.sep_cell
        
        if sep_row is not None:
            self.sep_row = sep_row
        if sep_cell is not None:
            self.sep_cell = sep_cell
        
        try:
            # HTML 파싱
            self.feed(html)
            result = "".join(self.out).strip()
        finally:
            # 원래 구분자로 복원
            self.sep_row = original_sep_row
            self.sep_cell = original_sep_cell
            # 파서 상태 초기화
            self.reset()
            
        return result

# 사용 예시
def extract_table_text(html_content, row_sep="\n", cell_sep=" "):
    """
    편의 함수: HTML 테이블에서 텍스트만 추출
    
    Args:
        html_content (str): HTML 테이블 문자열
        row_sep (str): 행 구분자 (기본: 줄바꿈)
        cell_sep (str): 셀 구분자 (기본: 공백)
    
    Returns:
        str: 테이블 태그가 제거된 순수 텍스트
    """
    return TableTextExtractor().table_html_to_text(html_content, row_sep, cell_sep)


def denormalizer(bbox, width, height):
    """
    Scales the bounding box coordinates to be relative to the image dimensions.

    Parameters:
    bbox (tuple): A tuple containing the bounding box coordinates (x_min, y_min, x_max, y_max).
    width (int): The width of the image.
    height (int): The height of the image.

    Returns:
    tuple: A tuple containing the scaled bounding box coordinates (x_min_scaled, y_min_scaled, x_max_scaled, y_max_scaled).
    """
    assert len(bbox) == 4, "Bounding box must be a tuple of four elements (x_min, y_min, x_max, y_max)."
    assert bbox[0] < bbox[2], "x_min must be less than x_max."
    assert bbox[1] < bbox[3], "y_min must be less than y_max."
    assert bbox[0] >= 0 and bbox[2] <= 1, "Bounding box x-coordinates must be within 0 to 1. \n Got: {}".format(bbox)
    assert bbox[1] >= 0 and bbox[3] <= 1, "Bounding box y-coordinates must be within 0 to 1. \n Got: {}".format(bbox)

    bbox[0] = bbox[0] * width
    bbox[1] = bbox[1] * height
    bbox[2] = bbox[2] * width
    bbox[3] = bbox[3] * height

    return bbox

def batch_denormalizer(bboxes, width, height):
    return [denormalizer(bbox, width, height) for bbox in bboxes]

def normalizer(bbox, width, height):
    """
    Descales the bounding box coordinates from relative to absolute image dimensions.

    Parameters:
    bbox (tuple): A tuple containing the bounding box coordinates (x_min, y_min, x_max, y_max).
    width (int): The width of the image.
    height (int): The height of the image.

    Returns:
    tuple: A tuple containing the descaled bounding box coordinates (x_min_descaled, y_min_descaled, x_max_descaled, y_max_descaled).
    """
    assert len(bbox) == 4, "Bounding box must be a tuple of four elements (x_min, y_min, x_max, y_max)."
    # assert bbox[0] < bbox[2], "x_min must be less than x_max."
    # assert bbox[1] < bbox[3], "y_min must be less than y_max."
    # assert bbox[0] >= 0 and bbox[2] <= width, f"Bounding box x-coordinates must be within 0 to {width}. bbox: {bbox}"
    # assert bbox[1] >= 0 and bbox[3] <= height, f"Bounding box y-coordinates must be within 0 to {height}. bbox: {bbox}"
    bbox[0] = min(max(bbox[0], 0), width)
    bbox[1] = min(max(bbox[1], 0), height)
    bbox[2] = min(max(bbox[2], 0), width)
    bbox[3] = min(max(bbox[3], 0), height)

    bbox[0] = bbox[0] / width
    bbox[1] = bbox[1] / height
    bbox[2] = bbox[2] / width
    bbox[3] = bbox[3] / height

    return bbox

def batch_normalizer(bboxes, width, height):
    return [normalizer(bbox, width, height) for bbox in bboxes]

def cxcywh_to_x1y1x2y2(bbox):
    """
    Converts bounding box from (center_x, center_y, width, height) format to (x_min, y_min, x_max, y_max) format.

    Parameters:
    bbox (tuple): A tuple containing the bounding box in (center_x, center_y, width, height) format.

    Returns:
    tuple: A tuple containing the bounding box in (x_min, y_min, x_max, y_max) format.
    """
    assert len(bbox) == 4, "Bounding box must be a tuple of four elements (center_x, center_y, width, height)."
    cx, cy, w, h = bbox
    x_min = cx - w / 2
    y_min = cy - h / 2
    x_max = cx + w / 2
    y_max = cy + h / 2
    return (x_min, y_min, x_max, y_max)

def batch_cxcywh_to_x1y1x2y2(bboxes):
    return [cxcywh_to_x1y1x2y2(bbox) for bbox in bboxes]

def cxcyx1y1_to_x1y1x2y2(bbox):
    """
    Converts bounding box from (center_x, center_y, x_min, y_min) format to (x_min, y_min, x_max, y_max) format.

    Parameters:
    bbox (tuple): A tuple containing the bounding box in (center_x, center_y, x_min, y_min) format.

    Returns:
    tuple: A tuple containing the bounding box in (x_min, y_min, x_max, y_max) format.
    """
    assert len(bbox) == 4, "Bounding box must be a tuple of four elements (center_x, center_y, x_min, y_min)."
    cx, cy, x_min, y_min = bbox
    w = 2 * (cx - x_min)
    h = 2 * (cy - y_min)
    x_max = x_min + w
    y_max = y_min + h
    return (x_min, y_min, x_max, y_max)

def batch_cxcyx1y1_to_x1y1x2y2(bboxes):
    return [cxcyx1y1_to_x1y1x2y2(bbox) for bbox in bboxes]