import json
import os
import shutil
import sys
import tempfile
import warnings
from enum import Enum
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Union

try:
    from . import utils
except ImportError:
    import utils

def parse_pdf_to_data(
    pdf_path: str,
    lang: str = "ko",
    parse_method: str = "auto",
    backend: str = "pipeline",
    keep_files: bool = False,
) -> Dict[str, Union[List, Dict, str, None]]:
    """
    mineru 2.x와 magic-pdf 1.x를 자동 감지하여 PDF를 파싱하고 dict로 반환한다.

    Args:
        pdf_path: PDF 파일 경로
        lang: 언어 설정 ("auto" | "txt" | "ocr")
        parse_method: 파싱 방법
        backend: 백엔드 ("pipeline" | "vlm-transformers" | "vlm-http-client")
        keep_files: 산출물 보존 여부

    Returns:
        Dict containing content_list, model, middle, markdown_path, outputs_dir
    """
    pdf = Path(pdf_path).resolve()
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")
        
    stem = pdf.stem
    out_dir = Path(tempfile.mkdtemp(prefix="mineru_out_")).resolve()
    
    try:
        # mineru 2.x 우선 시도
        from mineru.cli.client import do_parse as do_parse_mineru
        from mineru.cli.common import read_fn as read_fn_mineru
        
        pdf_bytes = read_fn_mineru(pdf)
        do_parse_mineru(
            output_dir=str(out_dir),
            pdf_file_names=[stem],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=[lang],
            backend=backend,
            parse_method=parse_method,
            p_formula_enable=False,
            p_table_enable=False,
        )
    except ModuleNotFoundError:
        # magic-pdf 1.x로 폴백
        from magic_pdf.tools.common import do_parse as do_parse_magic
        
        pdf_bytes = pdf.read_bytes()
        do_parse_magic(
            str(out_dir),
            stem,
            pdf_bytes,
            [],  # model_list
            parse_method,
            True,  # debug_able (파일 덤프 on)
            lang=lang,
            formula_enable=True,
            table_enable=True,
            # 덤프 스위치들
            f_dump_md=True,
            f_dump_content_list=True,
            f_dump_model_json=True,
            f_dump_middle_json=True,
        )
    
    # 산출물 찾기 및 로드
    candidates = list(out_dir.glob(f"**/{stem}_content_list.json"))
    content_json = candidates[0] if candidates else None
    
    def _read_json(path: Optional[Path]) -> Optional[Union[Dict, List]]:
        """JSON 파일을 안전하게 읽는 헬퍼 함수"""
        if path and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                warnings.warn(f"JSON 파일 읽기 실패: {path}, 오류: {e}")
        return None
    
    # 결과 파일들 로드
    content = _read_json(content_json)
    model = _read_json(content_json.parent / f"{stem}_model.json" if content_json else None)
    middle = _read_json(content_json.parent / f"{stem}_middle.json" if content_json else None)
    md_path = content_json.parent / f"{stem}.md" if content_json else None

    
    result = {
        "content_list": content,
        "model": model,
        "middle": middle,
        "markdown_path": str(md_path) if (md_path and md_path.exists()) else None,
        "outputs_dir": str(content_json.parent if content_json else out_dir),
    }
    
    # 파일 정리
    if not keep_files:
        shutil.rmtree(out_dir, ignore_errors=True)
        result["markdown_path"] = None
        result["outputs_dir"] = None
        
    return result


class BlockType(Enum):
    """블록 타입 열거형 - 타입 안전성과 IDE 지원 향상"""
    TEXT = "text"
    TITLE = "title"
    TABLE = "table"
    TABLE_BODY = "table_body"
    TABLE_CAPTION = "table_caption"
    TABLE_FOOTNOTE = "table_footnote"
    IMAGE = "image"
    IMAGE_BODY = "image_body"
    IMAGE_CAPTION = "image_caption"
    IMAGE_FOOTNOTE = "image_footnote"
    CODE = "code"
    CODE_BODY = "code_body"
    CODE_CAPTION = "code_caption"
    INTERLINE_EQUATION = "interline_equation"
    LIST = "list"
    INDEX = "index"
    REF_TEXT = "ref_text"


# 상수로 정의하여 매번 생성하지 않도록 최적화
LABEL_MAPPING = {
    BlockType.TEXT: 0,
    BlockType.TITLE: 1,
    BlockType.TABLE: 2,
    BlockType.TABLE_BODY: 2,
    BlockType.TABLE_CAPTION: 3,
    BlockType.TABLE_FOOTNOTE: 4,
    BlockType.IMAGE: 5,
    BlockType.IMAGE_BODY: 5,
    BlockType.IMAGE_CAPTION: 6,
    BlockType.IMAGE_FOOTNOTE: 7,
    BlockType.CODE: 8,
    BlockType.CODE_BODY: 8,
    BlockType.CODE_CAPTION: 9,
    BlockType.INTERLINE_EQUATION: 10,
    BlockType.LIST: 11,
    BlockType.INDEX: 12,
    BlockType.REF_TEXT: 13,
}

LABEL_NAMES = {
    0: "text",
    1: "title", 
    2: "table_body",
    3: "table_caption",
    4: "table_footnote",
    5: "image_body",
    6: "image_caption", 
    7: "image_footnote",
    8: "code_body",
    9: "code_caption",
    10: "equation",
    11: "list",
    12: "index",
    13: "ref_text",
    14: "discarded",
    15: "list_item"
}

# 블록 타입 집합으로 성능 최적화 (O(1) 조건 체크)
NESTED_BLOCK_TYPES = {BlockType.TABLE.value, BlockType.IMAGE.value, BlockType.CODE.value}
SIMPLE_BLOCK_TYPES = {
    BlockType.TITLE.value, BlockType.TEXT.value, BlockType.REF_TEXT.value, 
    BlockType.INTERLINE_EQUATION.value, BlockType.INDEX.value
}


def get_text(block: Dict) -> str:
    """
    블록에서 텍스트를 효율적으로 추출하는 함수
    
    Args:
        block: 블록 딕셔너리
        
    Returns:
        추출된 텍스트 문자열
    """
    if not block.get('lines'):
        return ''
    
    text_parts = []
    for line in block['lines']:
        for span in line.get('spans', []):
            # 우선순위: content > html > text
            text_content = span.get('content') or span.get('html') or span.get('text', '')
            if text_content:
                if span.get('type') == 'inline_equation':
                    text_parts.append(f"${text_content}$")
                else:
                    text_parts.append(text_content)

    return ' '.join(text_parts)



def extract_bbox_data_from_page(
    page_info: Dict, 
    default_score: float
) -> List[Dict]:
    """
    단일 페이지에서 바운딩 박스 데이터를 효율적으로 추출합니다.
    
    Args:
        page_info: 페이지 정보 딕셔너리
        default_score: 기본 신뢰도 점수
        
    Returns:
        바운딩 박스 데이터 리스트
    """
    gen_info = []
    page_size = page_info.get("page_size")
    if not page_size:
        warnings.warn("페이지 크기 정보가 없습니다.")
        return gen_info
        
    width, height = page_size
    reading_order = 0

    # 1. discarded_blocks 처리
    for dropped_bbox in page_info.get('discarded_blocks', []):
        bbox = dropped_bbox.get('bbox')
        if bbox is not None:
            normalized_bbox = utils.normalizer(bbox, width, height)
            gen_info.append({
                "bbox": normalized_bbox,
                "type": dropped_bbox.get('type', 'discarded'),
                "text": get_text(dropped_bbox),
                "reading_order": reading_order,
                "score": default_score * 0.5,
                "page_no": page_info.get("page_idx", -1)
            })
            reading_order += 1

    # 2. para_blocks 처리
    for block in page_info.get("para_blocks", []):
        block_type = block.get("type")
        bbox = block.get("bbox")
        
        if block_type in NESTED_BLOCK_TYPES:
            # TABLE, IMAGE, CODE의 중첩 블록 처리
            reading_order = _process_nested_blocks(
                block, width, height, default_score, reading_order, gen_info
            )
        elif block_type in SIMPLE_BLOCK_TYPES:
            # 단순 블록 처리
            if bbox is not None:
                normalized_bbox = utils.normalizer(bbox, width, height)
                # if "in other words, there" in get_text(block).lower():
                #     print(get_text(block))
                gen_info.append({
                    "bbox": normalized_bbox,
                    "type": block_type,
                    "text": get_text(block),
                    "reading_order": reading_order,
                    "score": default_score
                })
                reading_order += 1
        elif block_type == BlockType.LIST.value:
            # LIST 블록 특별 처리
            reading_order = _process_list_block(
                block, width, height, default_score, reading_order, gen_info
            )
        else:
            warnings.warn(f"알 수 없는 블록 타입: {block_type}")

    
           
    return gen_info


def _process_nested_blocks(
    block: Dict, 
    width: float, 
    height: float, 
    default_score: float, 
    reading_order: int, 
    gen_info: List[Dict]
) -> int:
    """중첩 블록 처리 헬퍼 함수"""
    for nested_block in block.get("blocks", []):
        nested_bbox = nested_block.get("bbox")
        if nested_bbox is None:
            continue
            
        nested_type = nested_block.get("type")
        
        # CROSS_PAGE 플래그 확인
        if (nested_type == BlockType.TABLE_FOOTNOTE.value and 
            nested_block.get("CROSS_PAGE", False)):
            continue

        normalized_bbox = utils.normalizer(nested_bbox, width, height)
        gen_info.append({
            "bbox": normalized_bbox,
            "type": nested_type,
            "text": get_text(nested_block),
            "reading_order": reading_order,
            "score": default_score
        })
        reading_order += 1
    
    return reading_order


def _process_list_block(
    block: Dict, 
    width: float, 
    height: float, 
    default_score: float, 
    reading_order: int, 
    gen_info: List[Dict]
) -> int:
    """LIST 블록 처리 헬퍼 함수"""
    bbox = block.get("bbox")
    if bbox is None:
        return reading_order
        
    if "blocks" in block:
        # sub_blocks가 있는 경우
        for sub_block in block["blocks"]:
            sub_bbox = sub_block.get("bbox")
            if sub_bbox is not None:
                normalized_bbox = utils.normalizer(sub_bbox, width, height)
                gen_info.append({
                    "bbox": normalized_bbox,
                    "type": BlockType.LIST.value,
                    "text": get_text(sub_block),
                    "reading_order": reading_order,
                    "score": default_score * 0.8
                })
                reading_order += 1
    else:
        # 일반 LIST 블록
        normalized_bbox = utils.normalizer(bbox, width, height)
        gen_info.append({
            "bbox": normalized_bbox,
            "type": BlockType.LIST.value,
            "text": get_text(block),
            "reading_order": reading_order,
            "score": default_score
        })
        reading_order += 1
    
    return reading_order


def reorder_discarded_blocks(data: List[Dict]) -> List[Dict]:
    """
    discarded 블록을 적절한 위치에 재배치하는 최적화된 함수
    
    Args:
        data: 블록 데이터 리스트
        
    Returns:
        재배치된 블록 데이터 리스트
    """
    result = []
    discarded_blocks = []
    
    # discarded 블록과 일반 블록 분리
    for block in data:
        if block['type'] == 'discarded':
            discarded_blocks.append(block)
        else:
            # 현재 블록보다 위에 있는 discarded 블록들을 먼저 추가
            for discarded in discarded_blocks[:]:
                if block['bbox'][1] > discarded['bbox'][3]:  # y1 > discarded_y2
                    result.append(discarded)
                    discarded_blocks.remove(discarded)
            
            result.append(block)
    
    # 남은 discarded 블록들 추가
    result.extend(discarded_blocks)
    
    return result


def extract_bbox_data_from_pdf_info(
    pdf_info: List[Dict], 
    default_score: float = 0.9
) -> List[List[Dict]]:
    """
    전체 PDF 정보에서 페이지별 바운딩 박스 데이터를 추출합니다.
    
    Args:
        pdf_info: PDF 페이지 정보 리스트
        default_score: 기본 신뢰도 점수
        
    Returns:
        페이지별 바운딩 박스 데이터 리스트
    """
    return [
        extract_bbox_data_from_page(page_info, default_score)
        for page_info in pdf_info
    ]


def load_and_extract_from_json(
    pdf_path: str, 
    default_score: float = 0.9
) -> List[List[Dict]]:
    """
    PDF 파일을 파싱하고 바운딩 박스 데이터를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로 (현재는 하드코딩된 "sample.pdf" 사용)
        default_score: 기본 신뢰도 점수
        
    Returns:
        페이지별 바운딩 박스 데이터 리스트
    """
    try:
        # TODO: pdf_path 파라미터를 실제로 사용하도록 수정 필요
        data = parse_pdf_to_data(
            pdf_path, 
            lang="korean", 
            parse_method="auto", 
            backend="pipeline", 
            keep_files=True
        )
        
        middle_data = data.get("middle")
        output_dir = data['outputs_dir']
        # md_data = open(data["markdown_path"], "r", encoding="utf-8").read() if data.get("markdown_path") else None
        # print(data.get("markdown_path"))
        # print(data["markdown_path"])
        # print(md_data)
        # print(data['outputs_dir'])
        # exit(True)

        if not middle_data:
            raise ValueError("middle 데이터를 찾을 수 없습니다.")
        
        # 디버그 출력 제거
        # print(middle_data)
        with open("debug_middle.json", "w", encoding="utf-8") as f:
            json.dump(middle_data, f, indent=4, ensure_ascii=False)
        
        pdf_info = middle_data.get("pdf_info", middle_data)
        if not isinstance(pdf_info, list):
            raise ValueError("pdf_info는 리스트 형태여야 합니다.")
        
        return extract_bbox_data_from_pdf_info(pdf_info, default_score), output_dir
        
    except Exception as e:
        warnings.warn(f"PDF 파싱 실패: {e}")
        return []


def mineru_parser(pdf_path: str) -> Dict[str, List[List[Dict]]]:
    """
    여러 PDF 파일을 파싱하여 결과를 딕셔너리로 반환합니다.
    
    Args:
        pdf_paths: PDF 파일 경로 리스트
        
    Returns:
        파일명을 키로 하는 파싱 결과 딕셔너리
    """
    result, output_dir = load_and_extract_from_json(pdf_path)
    # try:
    #     result = {load_and_extract_from_json(pdf_path)}
    # except Exception as e:
    #     warnings.warn(f"PDF 파싱 실패 {pdf_path}: {e}")
    #     # 빈 결과로 처리하여 전체 프로세스가 중단되지 않도록 함
    #     result = {}
    
    return result, output_dir


def main():
    """메인 실행 함수 - 향상된 에러 처리와 사용자 피드백"""
    # 설정
    pdf_pattern = "/data/docparser_mh/data/OCR/val_doc/images/*.jpg"
    output_dir = "../data/prediction/mineru_ocr/"
    os.makedirs(output_dir, exist_ok=True)
    input_type = "mineru"
    
    print(f"🔍 PDF 파일 검색 중: {pdf_pattern}")
    
    # PDF 파일 찾기
    pdf_paths = glob(pdf_pattern)
    if not pdf_paths:
        print(f"❌ PDF 파일을 찾을 수 없습니다: {pdf_pattern}")
        print("현재 디렉토리의 파일들:")
        for file in Path(".").glob("*"):
            print(f"  - {file}")
        return
    
    print(f"📁 발견된 파일들: {pdf_paths}")
    
    # 출력 디렉토리 생성
    os.makedirs(output_dir, exist_ok=True)
    
    # PDF 파싱 및 결과 저장
    print(f"\n🚀 PDF 파싱 시작...")
    try:
        result_dict, output_dir = mineru_parser(pdf_paths)
        output_file = Path(output_dir) / f"{input_type}_to_gen.json"
        
        # 결과 통계
        total_pages = sum(len(pages) for pages in result_dict.values())
        total_blocks = sum(
            sum(len(page_blocks) for page_blocks in pages) 
            for pages in result_dict.values()
        )
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4, ensure_ascii=False)
        
        print(f"\n✅ 처리 완료!")
        print(f"📄 총 페이지 수: {total_pages}")
        print(f"📦 총 블록 수: {total_blocks}")
        print(f"💾 결과 파일: {output_file}")
        
    except Exception as e:
        print(f"❌ 처리 중 오류 발생: {e}")


if __name__ == "__main__":
    main()
