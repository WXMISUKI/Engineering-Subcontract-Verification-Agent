import fitz

from app.plus.blockchain_supervision.seal_detect import _local_seal_detect


def test_standalone_detects_a_synthetic_red_seal(tmp_path):
    path = tmp_path / 'synthetic.pdf'
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.draw_circle(fitz.Point(300, 400), 60, color=(1, 0, 0), width=5)
    document.save(path)
    document.close()

    seals = _local_seal_detect(str(path))
    assert any(seal['page_idx'] == 1 and len(seal['bbox']) == 4 for seal in seals)
