"""Nepali word -> (tag, stem), without a trained model.

This adapts the Hindi approach of POS tagging followed by noun-phrase chunking
(NLTK's TnT tagger trained on the tagged `hindi.pos` corpus, then
`NP:{<NN.*>}` chunks as keywords). Two things stop it carrying over directly:

- NLTK ships no tagged Nepali corpus to train a tagger on.
- Nepali writes case markers attached to the noun (सरकारले, नेपालका,
  मन्त्रालयमा), where Hindi writes them as separate words (के, ने, में).

So tagging here is rule-based, and good enough for chunking:

    closed list of function words                      -> FW
    closed list of verb forms, or a verb ending         -> VB
    anything else                                       -> NN

and the case marker is stripped into the stem. A stripped marker also tells
the chunker the noun phrase ends there.
"""

from __future__ import annotations

import re
import unicodedata

from app.nlp import FW, NN, VB

FUNCTION_WORDS = frozenset(
    """
    र तथा वा अथवा तर पनि भने कि नै त मात्र मात्रै समेत लगायत लगायतका सहित एवं एवम्
    साथै उक्त सो सोही सोको
    यो त्यो यी ती यस त्यस यसको त्यसको यसले त्यसले यसमा त्यसमा यसका त्यसका यसलाई
    त्यसलाई यसबाट उनी उनले उनको उनका उनलाई उनीहरू उहाँ उहाँले उहाँको म मैं मैले मेरो
    हामी हामीले हाम्रो तपाईं तिमी आफ्नो आफ्ना आफ्नै आफू आफैं आफै जो जुन जस जसले जसको
    जसमा जसका जसलाई कसै कसैले कुन के को कसले कसरी किन कहिले कहाँ कति यस्तो यस्ता
    यस्ती त्यस्तो त्यस्ता यही त्यही यहाँ त्यहाँ यसरी त्यसरी
    सबै केही कुनै अरू अरु अन्य विभिन्न धेरै थोरै अझ अझै झन् दुवै प्रत्येक हरेक एक दुई
    तीन चार पाँच
    अब अहिले हाल हालै पहिले पछि अघि आज हिजो भोलि बेला बेलामा बिहान दिउँसो बेलुका राति
    क्रममा विषयमा रूपमा रुपमा अनुसार लागि लागी बीच बीचमा सम्म देखि बाट सँग संग भित्र
    बाहिर माथि तल विरुद्ध बारे बारेमा मार्फत द्वारा प्रति नजिक वरिपरि बिना तर्फ तर्फबाट
    बमोजिम अन्तर्गत अन्तर्गतको मा को का की ले लाई
    यद्यपि किनभने किनकि जब तब यदि भन्दा झैं जस्तै जस्तो जस्ता
    श्री श्रीमान् डा प्रा मिति गते साल जना वटा ओटा
    नयाँ पुरानो पुराना ठूलो ठूला सानो साना विशेष आवश्यक महत्त्वपूर्ण महत्वपूर्ण प्रभावकारी
    सकारात्मक अत्यधिक सम्बन्धित सुरु जारी सम्पन्न उपलब्ध उपस्थित
    आइतबार सोमबार मंगलबार मङ्गलबार बुधबार बिहीबार बिहिबार शुक्रबार शनिबार
    बैशाख वैशाख जेठ असार साउन श्रावण भदौ भाद्र असोज आश्विन कात्तिक कार्तिक मंसिर
    मङ्सिर पुस पुष माघ फागुन फाल्गुन चैत चैत्र
    """.split()
)

VERB_FORMS = frozenset(
    """
    छ छन् छु छौ छैन छैनन् हो होइन होइनन् हुन हुन् हुने हुनेछ हुन्छ हुन्छन् हुँदै हुँदा
    भयो भए भएको भएका भएकी भई भएर भएपछि भएकाले थियो थिए थिइन् थिएन थिएनन् रहेछ
    रहेको रहेका रहेकी रहे रहन्छ गर्न गर्ने गरेको गरेका गरिएको गरिने गर्दै गर्दा गरी गरेर
    गरे गर्छ गर्छन् गर्नु गर्नुपर्ने गर्यो गरियो गरिनेछ गर्नुभयो गराउन गराउने बनाउन बनाउने
    बताए बताइन् बताउनुभयो बताउँदै जनाए जनाएको जनाइन् सक्छ सक्छन् सक्ने सकिने सकिन्छ सके
    लागेको लागे लाग्ने पर्छ पर्ने परेको परे पाउने पाए दिए दिने दिएको लिए लिने लिएको
    आएको आए आउने आउँदा गएको गए जाने जाँदा भन्ने भनिएको भन्दै
    """.split()
)

# Inflection endings that, on a word of more than one syllable, mark a verb.
VERB_ENDINGS = (
    "ेको", "एको", "ेका", "एका", "ेकी", "एकी", "ेपछि", "एपछि", "ुभयो",
    "्ने", "िने", "ुने", "उने", "्नु", "उनु", "्न", "उन", "नेछ",
    "्दै", "ँदै", "्दैन", "ँदैन", "ँदा", "्छ", "्छन्", "ँछ", "ँछन्", "िन्छ",
    "िन्", "इन्", "्यो", "िए", "िएन", "ाएर", "एर", "ेर",
)

# Nouns that happen to end like a verb.
VERB_EXCEPTIONS = frozenset("प्रश्न प्रयत्न यत्न रत्न चिह्न स्वच्छ".split())

# Case markers and plural/honorific endings, stripped from nouns one at a time
# (कार्यक्रमहरूमा -> कार्यक्रमहरू -> कार्यक्रम).
CASE_SUFFIXES = tuple(
    sorted(
        """
        विरुद्ध मार्फत द्वारा भित्र देखि सम्म सँगै लाई बाट सँग संग बीच पछि तर्फ माथि
        हरू हरु ज्यू को का ले मा
        """.split(),
        key=len,
        reverse=True,
    )
)

# Words that only look like they carry a marker. नगरपालिका is not नगरपालि + का.
PROTECTED_WORDS = frozenset(
    "सीमा बीमा प्रतिमा गरिमा महिमा पूर्णिमा जम्मा क्षमा उपमा मेक्सिको मोरक्को श्रीलङ्का "
    "लङ्का ढाका नाका बाँकी उपत्यका पताका".split()
)
PROTECTED_ENDINGS = ("िका", "र्मा", "त्मा", "श्मा", "ष्मा", "क्का", "ङ्का", "ंका")

# Consonants and independent vowels -- one per syllable, near enough.
_LETTER_RE = re.compile(r"[ऄ-हक़-ॡॲ-ॿ]")
_BROKEN_CONJUNCT_RE = re.compile(r"्\s+(?=[क-ह])")


def normalize(text: str) -> str:
    """Make the same word compare equal however a site typed it."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‌", "").replace("‍", "")
    # Two-part vowel signs typed as separate pieces ("जनाकाे").
    text = text.replace("ाे", "ो").replace("ाै", "ौ")
    # A virama then a space is a broken conjunct ("पुर् याएका"), not two words.
    return _BROKEN_CONJUNCT_RE.sub("्", text)


def stem(word: str) -> str:
    """Strip case markers, keeping at least two syllables of root."""
    current = word
    for _ in range(3):
        if current in PROTECTED_WORDS or current.endswith(PROTECTED_ENDINGS):
            break
        for suffix in CASE_SUFFIXES:
            if not current.endswith(suffix):
                continue
            candidate = current[: -len(suffix)]
            if len(_LETTER_RE.findall(candidate)) >= 2:
                current = candidate
                break
        else:
            break
    return current


def _is_inflected_verb(word: str) -> bool:
    if word in VERB_EXCEPTIONS:
        return False
    return any(word.endswith(ending) and len(word) > len(ending) + 1 for ending in VERB_ENDINGS)


def tag(word: str) -> tuple[str, str]:
    """(tag, stem) for one Devanagari word. Verbs and function words keep
    their surface form; only nouns are stemmed."""
    if word in FUNCTION_WORDS:
        return FW, word
    if word in VERB_FORMS or _is_inflected_verb(word):
        return VB, word
    root = stem(word)
    if root in FUNCTION_WORDS:
        return FW, root
    if root in VERB_FORMS:
        return VB, root
    return NN, root
