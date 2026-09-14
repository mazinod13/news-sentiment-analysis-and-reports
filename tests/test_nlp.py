"""Keyword extraction and criticality grading. Offline; grades use the shipped lexicon."""

from __future__ import annotations

import copy

import pytest

from app.nlp import FW, NN, NUM, PUNC, VB
from app.nlp.analysis import analyse
from app.nlp.criticality import LexiconError, load_lexicon, parse_lexicon
from app.nlp.keywords import extract_keywords, noun_phrases
from app.nlp.nepali import normalize, stem
from app.nlp.tokens import tokenize
from app.parsing.article import extract
from app.settings import ROOT


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon(ROOT / "config" / "criticality.yaml")


@pytest.fixture
def story_body(load_outlet, fixture_text):
    def _body(source_id: str) -> str:
        _, selectors = load_outlet(source_id)
        html = fixture_text(f"{source_id}_story.html")
        return extract(html, selectors, source_id=source_id).body

    return _body


class TestNepaliWords:
    def test_two_part_vowel_sign_is_joined(self):
        assert normalize("जनाकाे") == "जनाको"

    def test_broken_conjunct_is_rejoined(self):
        assert normalize("पुर् याएका") == "पुर्याएका"

    @pytest.mark.parametrize(
        "word, expected",
        [
            ("नेपालका", "नेपाल"),
            ("सरकारले", "सरकार"),
            ("कार्यक्रमहरूमा", "कार्यक्रम"),
            ("अर्यालज्यूले", "अर्याल"),
            ("विश्वविद्यालयबीच", "विश्वविद्यालय"),
            ("विपद्को", "विपद्"),
            ("महानगरपालिकाले", "महानगरपालिका"),
        ],
    )
    def test_case_markers_are_stripped(self, word, expected):
        assert stem(word) == expected

    @pytest.mark.parametrize(
        "word", ["नगरपालिका", "उपत्यका", "सीमा", "शर्मा", "अमेरिका", "बाँकी", "जम्मा", "शंका"]
    )
    def test_words_that_only_look_marked_are_kept(self, word):
        assert stem(word) == word

    def test_tags(self):
        tagged = [(t.text, t.tag) for t in tokenize("सरकारले २०८३ मा निर्णय गरेको छ र भिडियो भयो ।")]
        assert tagged == [
            ("सरकारले", NN),
            ("२०८३", NUM),
            ("मा", FW),
            ("निर्णय", NN),
            ("गरेको", VB),
            ("छ", VB),
            ("र", FW),
            ("भिडियो", NN),
            ("भयो", VB),
            ("।", PUNC),
        ]


class TestKeywords:
    SENTENCE = (
        "गृह मन्त्रालय र काठमाडौं विश्वविद्यालयको मानसिक स्वास्थ्य विभागबीच मानसिक स्वास्थ्य "
        "अध्ययनका लागि समझदारीपत्रमा हस्ताक्षर भएको छ।"
    )

    def test_case_marker_closes_the_noun_phrase(self):
        assert noun_phrases(tokenize(self.SENTENCE)) == [
            ("गृह", "मन्त्रालय"),
            ("काठमाडौं", "विश्वविद्यालय"),
            ("मानसिक", "स्वास्थ्य", "विभाग"),
            ("मानसिक", "स्वास्थ्य", "अध्ययन"),
            ("समझदारीपत्र",),
            ("हस्ताक्षर",),
        ]

    def test_function_words_and_verbs_never_become_keywords(self):
        words = {
            word
            for keyword in extract_keywords([], tokenize(self.SENTENCE))
            for word in keyword.text.split()
        }
        assert not words & {"र", "लागि", "भएको", "छ"}

    def test_title_phrases_rank_first(self):
        title = tokenize("भोटेकोशीमा बाढी")
        body = tokenize(
            "नदी किनारमा बस्ती छ। भोटेकोशीमा बाढी आएको छ। नदी किनारमा सतर्कता अपनाउन आग्रह गरिएको छ।"
        )
        top_two = {keyword.text for keyword in extract_keywords(title, body)[:2]}
        assert top_two == {"भोटेकोशी", "बाढी"}

    def test_real_nepali_story(self, story_body):
        texts = [keyword.text for keyword in extract_keywords([], tokenize(story_body("moha")))]
        assert "गृह मन्त्रालय" in texts
        assert any(text.startswith("मानसिक स्वास्थ्य") for text in texts)

    def test_english(self):
        keywords = extract_keywords(
            tokenize("Bhote Koshi floods"),
            tokenize(
                "The Government continues search and rescue operations in Rasuwa "
                "after the Bhote Koshi floods."
            ),
        )
        texts = [keyword.text for keyword in keywords]
        assert texts[0] == "bhote koshi floods"
        assert "rescue operations" in texts


class TestCriticality:
    def test_disaster_with_deaths_is_a(self, lexicon):
        result = analyse(
            "सिन्धुपाल्चोकमा बाढी पहिरो, १२ जनाको मृत्यु",
            "सिन्धुपाल्चोकमा आएको बाढी र पहिरोमा परी १२ जनाको मृत्यु भएको छ भने ८ जना बेपत्ता छन्। "
            "उद्धार कार्य जारी छ।",
            lexicon,
        )
        assert result.grade == "A"
        tiers = {match.term: match.tier for match in result.criticality.matches}
        assert tiers["बाढी*"] == "critical"
        assert tiers["उद्धार*"] == "high"

    def test_english_disaster_is_a(self, lexicon):
        result = analyse(
            "Floods kill 12 in Rasuwa",
            "Floods killed 12 people and rescue teams are searching for the missing.",
            lexicon,
        )
        assert result.grade == "A"

    def test_routine_business_story_is_f(self, lexicon, story_body):
        result = analyse("टाटा कार्निभलको चौथो संस्करण सुरु हुँदै", story_body("annapurna-post"), lexicon)
        assert result.grade == "F"
        assert result.criticality.matches == ()

    def test_prevention_story_is_dampened(self, lexicon, story_body):
        result = analyse(
            "आत्महत्या न्यूनीकरण गर्न गृह मन्त्रालय र काठमाडौं विश्वविद्यालयबीच समझदारीपत्रमा हस्ताक्षर",
            story_body("moha"),
            lexicon,
        )
        assert result.criticality.dampened
        assert result.grade not in ("A", "B")

    def test_dampened_grade_is_capped_even_with_a_high_score(self, lexicon):
        result = analyse(
            "बाढी पहिरो न्यूनीकरण तालिम",
            "बाढी पहिरो र भूकम्पमा मृत्यु हुन नदिन पूर्वतयारी तालिम सम्पन्न भयो।",
            lexicon,
        )
        assert result.criticality.score >= lexicon.thresholds[0][1] * lexicon.dampener_multiplier
        assert result.grade == lexicon.dampened_max_grade

    def test_postposition_is_not_a_bomb(self, lexicon):
        matches = analyse("", "नियमावली बमोजिम कारबाही गरिने छ।", lexicon).criticality.matches
        assert "बम" not in {match.term for match in matches}

    def test_each_word_counts_for_its_most_severe_term_only(self, lexicon):
        matches = analyse("", "देशभर संकटकाल घोषणा गरियो।", lexicon).criticality.matches
        assert [match.tier for match in matches] == ["critical"]

    def test_repetition_is_capped(self, lexicon):
        once = analyse("", "सडक दुर्घटना भयो।", lexicon).criticality.score
        many = analyse("", "सडक दुर्घटना भयो। " * 20, lexicon).criticality.score
        assert many == pytest.approx(once * lexicon.per_term_cap)

    def test_grade_thresholds(self, lexicon):
        for grade, minimum in lexicon.thresholds:
            assert lexicon.grade_for(minimum) == grade
        assert lexicon.grade_for(0) == "F"


class TestLexiconValidation:
    BASE = {
        "grades": {"A": 10, "B": 8, "C": 6, "D": 4, "E": 2, "F": 0},
        "tiers": {"high": {"weight": 3, "terms": {"en": ["flood*"], "ne": ["बाढी*"]}}},
    }

    def data(self):
        return copy.deepcopy(self.BASE)

    def test_minimal_lexicon_parses(self):
        assert len(parse_lexicon(self.data()).terms) == 2

    def test_every_grade_needs_a_threshold(self):
        data = self.data()
        del data["grades"]["E"]
        with pytest.raises(LexiconError, match="A, B, C, D, E, F"):
            parse_lexicon(data)

    def test_thresholds_must_descend(self):
        data = self.data()
        data["grades"]["C"] = 9
        with pytest.raises(LexiconError, match="descend"):
            parse_lexicon(data)

    def test_weight_must_be_positive(self):
        data = self.data()
        data["tiers"]["high"]["weight"] = 0
        with pytest.raises(LexiconError, match="weight"):
            parse_lexicon(data)

    def test_prefix_needs_two_characters(self):
        data = self.data()
        data["tiers"]["high"]["terms"] = ["f*"]
        with pytest.raises(LexiconError, match="prefix"):
            parse_lexicon(data)

    def test_dampener_max_grade_must_be_a_grade(self):
        data = self.data()
        data["dampeners"] = {"multiplier": 0.5, "max_grade": "G", "terms": ["drill*"]}
        with pytest.raises(LexiconError, match="max_grade"):
            parse_lexicon(data)

    def test_missing_file_names_the_path(self, tmp_path):
        with pytest.raises(LexiconError, match="nope.yaml"):
            load_lexicon(tmp_path / "nope.yaml")
