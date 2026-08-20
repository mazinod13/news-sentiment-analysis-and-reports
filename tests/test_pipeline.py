"""Shared pipeline tests: URL canonicalisation, dedupe, config validation."""

from __future__ import annotations

import pytest
import yaml

from app.parsing.clean import clean, looks_devanagari, to_ascii_digits
from app.pipeline.dedupe import (
    NEAR_DUPLICATE_DISTANCE,
    dedupe_batch,
    from_signed64,
    hamming,
    is_near_duplicate,
    simhash,
    to_signed64,
)
from app.pipeline.normalize import canonical_url, url_hash
from app.sources import SourceConfigError, load_sources, parse_source


class TestCanonicalUrl:
    def test_upgrades_scheme_and_drops_www(self):
        assert canonical_url("http://www.Example.com/story/1/") == "https://example.com/story/1"

    def test_strips_tracking_params(self):
        assert canonical_url(
            "https://example.com/a?utm_source=fb&fbclid=xyz&id=7"
        ) == "https://example.com/a?id=7"

    def test_drops_fragment(self):
        assert canonical_url("https://example.com/a#comments") == "https://example.com/a"

    def test_same_article_two_ways_hashes_equal(self):
        assert url_hash("http://annapurnapost.com/story/505752") == url_hash(
            "https://www.annapurnapost.com/story/505752/?utm_source=rss"
        )


class TestDedupe:
    def test_identical_text_matches(self):
        text = "काठमाडौं : नेपालका लागी टाटा मोटर्सको एकमात्र आधिकारिक वितरक सिप्रदी ट्रेडिंगले"
        assert hamming(simhash(text), simhash(text)) == 0

    def test_small_edit_stays_near(self):
        """Same story, one sentence longer -- must stay under the threshold.

        The threshold is tuned for article-length text. Short strings have too
        few shingles for the distance to mean anything, which is why simhash
        runs over title + body and never the title alone.
        """
        base = (
            "काठमाडौं : नेपालका लागी टाटा मोटर्सको एकमात्र आधिकारिक वितरक सिप्रदी ट्रेडिंगले "
            "बिहिबारदेखि टाटा कार्निभलको चौथो संस्करण सुरु गर्न लागेको छ। भदौ ४ देखि ७ गतेसम्म "
            "चल्ने क्याम्पमा पुराना गाडीसँग हालै नेपालमा लन्च गरिएको नयाँ पन्च इभि र नयाँ "
            "टियागो इभिसँग साट्न सकिने कम्पनीले जनाएको छ। पुरानो गाडीलाई डाउनपेमेन्टको रुपमा "
            "राखेर नयाँ इलेक्ट्रिक गाडी लैजान सकिने व्यवस्था गरिएको छ। क्याम्पमा विशेष "
            "एक्सचेन्ज बोनस र फाइनान्सिङ सुविधा उपलब्ध हुनेछ।"
        )
        edited = base + " कम्पनीले थप जानकारी दिएको छ।"   # a syndicated copy's extra line
        assert hamming(simhash(base), simhash(edited)) <= NEAR_DUPLICATE_DISTANCE

    def test_different_stories_are_far_apart(self):
        """Unrelated stories must clear the threshold by a wide margin -- if
        this ever gets close, the threshold is too loose."""
        a = simhash(
            "नेपाल राष्ट्र बैंकले चालु आर्थिक वर्षको मौद्रिक नीतिको अर्धवार्षिक समीक्षा "
            "सार्वजनिक गरेको छ। बैंकले ब्याजदर कोरिडोरमा परिवर्तन गरेको छ र निजी "
            "क्षेत्रतर्फ जाने कर्जाको वृद्धिदर लक्ष्य पनि संशोधन गरेको छ।"
        )
        b = simhash(
            "त्रिभुवन अन्तर्राष्ट्रिय विमानस्थलमा खराब मौसमका कारण उडान अवरुद्ध भएपछि "
            "यात्रुहरू सास्तीमा परेका छन्। धेरै उडान रद्द भएका छन् र यात्रुहरूले "
            "टर्मिनलमै प्रतीक्षा गर्नुपरेको छ।"
        )
        assert hamming(a, b) > 2 * NEAR_DUPLICATE_DISTANCE

    def test_near_duplicate_lookup(self):
        known = [simhash("एउटै समाचार फरक साइटमा प्रकाशित भएको छ")]
        assert is_near_duplicate(simhash("एउटै समाचार फरक साइटमा प्रकाशित भएको छ"), known)
        assert not is_near_duplicate(simhash("बिल्कुलै फरक विषयको समाचार सामग्री"), known)

    def test_signed64_roundtrip(self):
        value = simhash("जुनसुकै पाठ")
        assert from_signed64(to_signed64(value)) == value
        assert -(2**63) <= to_signed64(value) < 2**63

    def test_batch_dedupe_keeps_first(self):
        items = [{"u": "a"}, {"u": "b"}, {"u": "a"}]
        assert len(dedupe_batch(items, key=lambda i: i["u"])) == 2


class TestClean:
    def test_strips_tags_and_entities(self):
        assert clean("<p>काठमाडौं&nbsp;:&amp; test</p>") == "काठमाडौं :& test"

    def test_devanagari_digits(self):
        assert to_ascii_digits("२०८३ भदौ ३") == "2083 भदौ 3"

    def test_script_detection(self):
        assert looks_devanagari("काठमाडौं")
        assert not looks_devanagari("Kathmandu Post")


class TestSourceConfig:
    def test_every_configured_source_is_valid(self, sources_dir):
        sources = load_sources(sources_dir)
        assert sources, "no outlets configured"

    def test_selector_pack_exists_for_every_source(self, sources_dir, selectors_dir):
        for source in load_sources(sources_dir).values():
            if source.selectors:
                assert (selectors_dir / f"{source.selectors}.yaml").exists(), source.id

    def test_id_must_match_filename(self, tmp_path):
        path = tmp_path / "wrong-name.yaml"
        data = yaml.safe_load(
            """
            id: right-name
            name: X
            url: https://x.test/rss
            method: rss
            lang: ne
            category: news
            priority: 1
            active: true
            """
        )
        with pytest.raises(SourceConfigError, match="must match the filename"):
            parse_source(data, path)

    def test_inactive_needs_a_reason(self, tmp_path):
        data = yaml.safe_load(
            """
            id: dead-feed
            name: Dead Feed
            url: https://x.test/rss
            method: rss
            lang: ne
            category: news
            priority: 3
            active: false
            """
        )
        with pytest.raises(SourceConfigError, match="inactive_reason"):
            parse_source(data, tmp_path / "dead-feed.yaml")
