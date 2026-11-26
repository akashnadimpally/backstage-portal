import os

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline


def build_model():
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=50_000, strip_accents="unicode")
    model_kind = os.getenv("MODEL_KIND", "tfidf_logistic")
    if model_kind == "tfidf_logistic":
        classifier = LogisticRegression(max_iter=1000, class_weight="balanced")
    elif model_kind == "tfidf_naive_bayes":
        classifier = MultinomialNB()
    else:
        raise ValueError(f"Unsupported NLP model kind '{model_kind}'")
    return make_pipeline(vectorizer, classifier)