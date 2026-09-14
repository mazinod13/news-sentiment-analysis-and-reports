"""Keyword extraction and criticality grading for Nepali and English news.

Tags used across app/nlp -- a deliberately small set, because all they need to
do is tell the noun-phrase chunker where a phrase starts and stops:

    NN    noun-like word: nouns, names, and content adjectives
    VB    verb or auxiliary
    FW    function word: conjunction, pronoun, postposition, time word
    NUM   number, in ASCII or Devanagari digits
    PUNC  punctuation
"""

NN, VB, FW, NUM, PUNC = "NN", "VB", "FW", "NUM", "PUNC"
