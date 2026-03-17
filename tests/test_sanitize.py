from zsxq_pdf.util.sanitize import sanitize_filename


def test_sanitize_filename_removes_invalid_chars():
    assert sanitize_filename('a<>:"/\\|?*b.pdf') == "a_________b.pdf"


def test_sanitize_filename_reserved_device_name():
    assert sanitize_filename("CON.txt").startswith("CON_")


def test_sanitize_filename_truncates():
    name = "a" * 500 + ".pdf"
    out = sanitize_filename(name, max_len=50)
    assert out.endswith(".pdf")
    assert len(out) <= 50
