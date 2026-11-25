"""Tesseract TSV is unquoted text; fake responses must not leak low-confidence rows."""
from types import SimpleNamespace

import pytest

from reel.stages import ocr


HEADER='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'


def word(text,confidence,index):
    return f'5\t1\t1\t1\t1\t{index}\t0\t0\t10\t10\t{confidence}\t{text}\n'


@pytest.fixture
def recognize_tsv(monkeypatch):
    response=SimpleNamespace(stdout='')
    image=object()
    monkeypatch.setattr(ocr.shutil,'which',lambda name:'/fake/tesseract' if name=='tesseract' else None)
    monkeypatch.setattr(ocr.cv2,'imwrite',lambda path,value: value is image)
    def run(command,**kwargs):
        assert command[0]=='tesseract' and command[-1]=='tsv'
        assert kwargs==dict(capture_output=True,text=True,check=True,timeout=45)
        return response
    monkeypatch.setattr(ocr.subprocess,'run',run)
    def recognize(rows,minimum=60):
        response.stdout=HEADER+''.join(rows)
        return ocr.recognize(image,minimum)
    return recognize


def test_literal_quote_cannot_merge_tsv_rows_or_bypass_word_confidence(recognize_tsv):
    result=recognize_tsv([word('"',99,1),word('leak_marker',12,2),word('Hash',98,3)])
    assert result=='" Hash'
    assert 'leak_marker' not in result and '\t' not in result and '\n5\t' not in result


def test_quote_pairs_are_glyphs_not_csv_string_delimiters(recognize_tsv):
    assert recognize_tsv([word('"Hash"',95,1),word('Table',96,2)])=='"Hash" Table'


@pytest.mark.parametrize('confidence',['nan','inf','-inf','not-a-number'])
def test_invalid_confidence_cannot_admit_text_or_discard_following_good_words(recognize_tsv,confidence):
    result=recognize_tsv([word('leak_marker',confidence,1),word('Hash',98,2)])
    assert result=='Hash'


def test_confidence_threshold_is_inclusive_and_decimal_values_remain_valid(recognize_tsv):
    assert recognize_tsv([word('lower',59.99,1),word('Hash',60,2),word('Table',60.01,3)])=='Hash Table'
