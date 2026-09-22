import pytest

from app.chunking import Page, UnreadableDocument, chunk_pages, extract

LOREM = (
    "Termination for convenience. Either party may terminate this Agreement upon thirty "
    "days written notice to the other party. No termination fee shall be payable. "
)


def doc(paragraphs: int, sentences: int = 6) -> str:
    return "\n\n".join(f"Section {i}. " + LOREM * sentences for i in range(paragraphs))


def test_offsets_round_trip_exactly():
    """A citation is only as good as its offsets — every chunk must slice back out of the source."""
    full, chunks = chunk_pages([Page(1, doc(12))], limit=400, overlap=60)
    assert chunks
    for c in chunks:
        assert full[c.char_start : c.char_end] == c.text


def test_chunks_cover_every_character_of_content():
    """Whitespace-only spans are dropped (embedding a newline is waste); content never is."""
    full, chunks = chunk_pages([Page(1, doc(12))], limit=400, overlap=0)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(full)

    covered = bytearray(len(full))
    for c in chunks:
        covered[c.char_start : c.char_end] = b"\x01" * (c.char_end - c.char_start)
    dropped = [i for i, ch in enumerate(full) if not covered[i] and not ch.isspace()]
    assert not dropped, f"content dropped at offsets {dropped[:5]}"

    for prev, nxt in zip(chunks, chunks[1:]):
        assert not full[prev.char_end : nxt.char_start].strip(), "a gap swallowed real text"


def test_overlap_extends_backwards_without_splitting_a_word():
    full, chunks = chunk_pages([Page(1, doc(12))], limit=400, overlap=60)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.char_start < prev.char_end, "overlap should reach back into the previous chunk"
        assert nxt.text[0] not in " \t\n"
        if nxt.char_start > 0:
            assert full[nxt.char_start - 1] in " \t\n", "chunk began mid-word"


def test_no_chunk_exceeds_the_limit_plus_its_overlap():
    limit, overlap = 400, 60
    _, chunks = chunk_pages([Page(1, doc(20))], limit=limit, overlap=overlap)
    for c in chunks:
        assert len(c.text) <= limit + overlap


def test_unseparated_text_is_hard_cut_rather_than_dropped():
    blob = "x" * 1000
    full, chunks = chunk_pages([Page(1, blob)], limit=300, overlap=0)
    assert "".join(c.text for c in chunks) == full
    assert all(len(c.text) <= 300 for c in chunks)


def test_pages_are_attributed_to_the_right_chunk():
    pages = [Page(1, doc(3)), Page(2, doc(3)), Page(3, doc(3))]
    full, chunks = chunk_pages(pages, limit=500, overlap=0)
    assert {c.page_start for c in chunks} == {1, 2, 3}
    for c in chunks:
        assert c.page_start <= c.page_end
        # the chunk's own text must actually live on the page it claims
        assert full.index(c.text) >= 0
    first_on_page_3 = next(c for c in chunks if c.page_start == 3)
    assert first_on_page_3.char_start > chunks[0].char_start


def test_unpaginated_formats_report_no_page():
    _, chunks = chunk_pages([Page(None, doc(4))], limit=400, overlap=0)
    assert all(c.page_start is None and c.page_end is None for c in chunks)


def test_short_document_is_one_chunk():
    _, chunks = chunk_pages([Page(1, "Just one short line.")], limit=400)
    assert len(chunks) == 1
    assert chunks[0].text == "Just one short line."


def test_plain_text_and_markdown_extract():
    assert extract(b"hello world", "notes.txt")[0].text == "hello world"
    assert extract(b"# Title", "readme.md")[0].number is None


def test_unsupported_type_is_rejected():
    with pytest.raises(UnreadableDocument):
        extract(b"\x00\x01", "photo.heic")


def test_a_sparse_document_does_not_collapse_into_one_unciteable_chunk():
    """Eight pages of one short line each would otherwise fit in a single chunk, and a
    citation reading 'pp. 1-8' points at nothing."""
    pages = [Page(i + 1, f"Clause {i + 1}. A short provision.") for i in range(8)]
    _, chunks = chunk_pages(pages, limit=4000, overlap=0, max_pages=3)

    assert len(chunks) > 1
    for c in chunks:
        assert (c.page_end - c.page_start) < 3, f"chunk spans pp. {c.page_start}-{c.page_end}"


def test_a_dense_document_is_unaffected_by_the_page_cap():
    """Every page already exceeds the limit, so grouping changes nothing."""
    pages = [Page(i + 1, doc(4)) for i in range(6)]
    with_cap = chunk_pages(pages, limit=400, overlap=0, max_pages=3)[1]
    no_cap = chunk_pages(pages, limit=400, overlap=0, max_pages=999)[1]
    assert [(c.char_start, c.char_end) for c in with_cap] == [(c.char_start, c.char_end) for c in no_cap]


def test_overlap_does_not_shift_the_cited_page_backwards():
    """The overlap reaches into the previous page. The citation must not follow it there."""
    pages = [Page(i + 1, f"MARKER{i + 1}. " + LOREM * 3) for i in range(5)]
    _, chunks = chunk_pages(pages, limit=4000, overlap=200, max_pages=1)

    assert len(chunks) == 5
    for c in chunks:
        assert c.page_start == c.page_end, f"single-page group reported pp. {c.page_start}-{c.page_end}"
        assert f"MARKER{c.page_start}." in c.text, "chunk cites a page its content is not on"
    assert [c.page_start for c in chunks] == [1, 2, 3, 4, 5]
