"""Main app module."""

import pickle
import time

import numpy as np
import treetaggerwrapper as ttw
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from nltk.corpus import stopwords

from .path_problem_resolver import get_absolute_path
from . import persistence
from . import postprocessing
from . import syuzhet
from .text_processing import text_to_sentences, sentence_to_words

CONFIG_FILE_PATH = get_absolute_path("config.json")

CMGR = persistence.configuration_manager.ConfigurationManager(CONFIG_FILE_PATH)
# CMGR = ConfigurationManager("config.json")

LANGUAGE = CMGR.get_default_language()
EMOTIONS_ARRAY_LENGTH = CMGR.get_emotion_array_length()
EMOTION_NAMES = CMGR.get_emotion_names()

DATA_DIR = CMGR.get_data_dir()
EMOLEX_FILENAME = CMGR.get_lexicon_filename(LANGUAGE)

EMOLEX_ABS_PATH = get_absolute_path('syuzhet/'
                                    + DATA_DIR + '/' + EMOLEX_FILENAME)

EMOLEX_ENH_FILENAME = CMGR.get_enhanced_lexicon_filename()
EMOLEX_ENH_PATH = get_absolute_path('syuzhet/' + DATA_DIR +
                                    '/' + EMOLEX_ENH_FILENAME)

with open(EMOLEX_ABS_PATH, 'rb') as f:
    EMOLEX = pickle.load(f)

with open(EMOLEX_ENH_PATH, 'rb') as f:
    EMOLEX_ENHANCED = pickle.load(f)

# Cache
CACHE = persistence.PersistencyManager()

# Main application
APP = Flask(__name__)
CORS(app=APP)


@APP.route('/', methods=['GET'])
def show_readme():
    """Show the help readme."""
    return APP.send_static_file('Readme.html')


@APP.route('/gui-test', methods=['GET'])
def send_gui_test():
    """Sent the text analysis page."""
    result = render_template("index.html")
    return result


@APP.route('/analyze', methods=['POST'])
def analyze_text():
    """Analyze a text and send response."""
    # if parsing unsuccessful, don't crash
    req_contents = request.get_json(silent=True)

    if req_contents:
        try:
            text_to_analyze = req_contents['content']
        except KeyError:
            response_dict = make_error_response("Malformed input JSON." +
                                                " Missing 'content' field.")
            return jsonify(response_dict)

        try:
            get_sents = req_contents['get_sentences']
            get_sentences = get_sents
        except KeyError:
            get_sentences = False

        try:
            preprocess = req_contents['preprocess_text']
        except KeyError:
            preprocess = False

        try:
            use_filter = req_contents['use_filter']
        except KeyError:
            use_filter = False

        try:
            lex_version = req_contents['lexicon_version']
        except KeyError:
            lex_version = 'base'

        analysis_result = _analyze(text_to_analyze,
                                   sent_strs=get_sentences,
                                   lex_version=lex_version,
                                   use_filter=use_filter,
                                   preprocess=preprocess)

        # Save the result in a file and store the cache name
        current_id = str(int(time.time()))
        CACHE.save_cache(np.array(analysis_result['sentences']),
                         current_id)

        result = {'aggregate': analysis_result['aggregate'].tolist(),
                  'emotions': _make_sent_result(analysis_result['sentences'])}

        try:
            postproc_flag = req_contents['postprocessing']
        except KeyError:
            postproc_flag = False

        if postproc_flag:
            try:
                n_harmonics = req_contents['number_of_harmonics']
                if not n_harmonics or n_harmonics == []:
                    n_harmonics = [5, 10, 15, 20]
            except KeyError:
                # set the default value
                n_harmonics = [5, 10, 15, 20]

            postproc = _postprocess(np.array(analysis_result['sentences']),
                                    n_harmonics)
            result['harmonics'] = _make_postproc_dict(postproc)

        if get_sentences:
            result['splitted_sentences'] = analysis_result['splitted_sentences']

        result_dict = make_result_dict(result, emo_names=EMOTION_NAMES,
                                       request_id=str(int(time.time())))
        response_json = jsonify(result_dict)
        return response_json
    else:
        raise Exception("Empty input JSON")


@APP.route('/postprocess', methods=['POST'])
def postprocess_result():
    """Postprocess the result of an analysis."""
    req_contents = request.get_json(silent=True)

    try:
        text_id = req_contents['text_id']
        if int(CACHE.cache_time) != text_id:
            err_response = make_error_response(
                "Invalid 'text_id', it should be {}".format(CACHE.cache_time))
            return jsonify(err_response)

        try:
            n_harmonics = req_contents['number_of_harmonics']
            ndarray = CACHE.load_cache()
            postproc = _postprocess(ndarray, n_harmonics)

            result = {'text_id': text_id,
                      'harmonics': _make_postproc_dict(postproc)}
            return jsonify(result)
        except KeyError:
            err_response = make_error_response(
                "Missing number of harmonics in request")
            return jsonify(err_response)
    except KeyError:
        err_response = make_error_response("Missing 'text_id' field.")
        return jsonify(err_response)


@APP.route('/lemmatize', methods=['POST'])
def lemmatize():
    """Lemmatize a text given in the input JSON."""
    req_contents = request.get_json(silent=True)

    try:
        text = req_contents["text"]

        try:
            delete_stopwords = req_contents["delete_stopwords"]
        except KeyError:
            delete_stopwords = True

        tagger = ttw.TreeTagger(TAGLANG=LANGUAGE.lower()[0:2],
                                TAGDIR=CMGR.get_treetagger_path())
        lemmatizer = syuzhet.Lemmatizer(tagger)

        splitted_into_sentences = text_to_sentences(text, LANGUAGE)

        def mapfun(sentence):
            """Map function for sentence_to_words."""
            return sentence_to_words(sentence, LANGUAGE)

        sentences = map(mapfun, splitted_into_sentences)
        lemmatized_sentences = [lemmatizer.lemmatize(s) for s in sentences]

        if delete_stopwords:
            stop_words = set(stopwords.words("italian"))
            stop_words_punct = stop_words.union(
                {",", ";", ".", ":", "?", "!", "'"})

            filtered_sentences_no_stopwords = [
                [word for word in sentence if word not in stop_words_punct]
                for sentence in lemmatized_sentences
            ]

            response = {"sentences": filtered_sentences_no_stopwords}
        else:
            response = {"sentences": lemmatized_sentences}

        return jsonify(response)
    except KeyError:
        err_response = make_error_response("Missing text to lemmatize.")
        return jsonify(err_response)


def make_result_dict(data, request_id=None, emo_names=None):
    """Create a JSON with the results and all other fields."""
    if data is None or data == []:
        res = make_error_response("Empty analysis result.")
        return res

    tpls = [('text_id', request_id),
            ('emotion_names', emo_names),
            ('result', data)]

    res = {k: v for k, v in tpls if v is not None}

    return res


def make_error_response(msg="Couldn't satisfy request."):
    """Return a dictionary for an error response.
    Used to return an informative error message as JSON
    for a failed request."""
    return {'error': msg}


def _make_sent_result(emotions):
    """Vertically stack the numpy arrays and make them list.

    Parameters
    ----------
    emotions: List[np.ndarray]
        list of arrays, each representing the emotions in a sentence

    Returns
    -------
    each_emo: dict[str: List[int]]
        dictionary whose keys are the emotion names, and values the value
        of the named emotion for each sentence
    """
    if emotions is None:
        return None

    tmp = np.stack(emotions)
    result = {EMOTION_NAMES[i]: tmp[:, i].tolist()
              for i in range(len(EMOTION_NAMES))}

    return result


def _postprocess(data, n_harmonics):
    """Postprocess the data.

    Parameters
    ----------
    data: np.ndarray
        ndarray where the i-th row is a sentence, and the
        j-th column is the emotion

    n_harmonics: List[int > 0]
        list of numbers of harmonics to use, or a single integer value

    Returns
    -------
    postprocessed: Dict[str: np.ndarray]
        dictionary where the keys are the number of harmonics used,
        values are the filtered emotions
    """
    if isinstance(n_harmonics, int):
        return _postprocess(data, [n_harmonics])

    if isinstance(n_harmonics, list):
        return {str(n):
                postprocessing.smooth_emotions(data,
                                               number_of_harmonics=n)
                for n in n_harmonics}


def _make_postproc_dict(postproc):
    """Make the appropriate dictionary based on the postprocessed data.

    Parameters
    ----------
    postproc: Dict[str: np.ndarray]
        keys are str(number of harmonics used in low pass filter), values are
        2D np.ndarrays where every column is the filtered emotion

    Returns
    -------
    result: Dict[str: Dict[str: List[float]]]
        dictionary where first-level keys are the numbers of harmonics,
        the second-level keys are the emotion names
    """
    return {n_harmonics: {EMOTION_NAMES[j]:
                          list(map((lambda x: x if x >= 1e-3 else 0),
                                   postproc[n_harmonics][:, j].tolist()))
                          for j in range(postproc[n_harmonics].shape[1])}
            for n_harmonics in postproc}


def _analyze(text, sent_list=False, sent_strs=False, lex_version='base',
             use_filter=True, preprocess=False):
    """Analyze a text using Syuzhet and TreeTagger.

    Parameters
    ----------
    text: str
        text to analyze

    sent_list: bool
        if True, add the list of sentences (as List[List[str]]) in the result

    sent_strs: bool
        if True, add the list of sentences (as List[str]) in the result

    lex_version: str
        either "base" (use base emolex) or "enhanced" (use ANN enhanced one)

    use_filter: bool
        if True, use smart search algorithm for emotion detection; else use
        simple sum of emotional values

    preprocess:
        preprocess text before NLTK tokenization, strip all dialog delimiters
        and substitute weak punctuation with full stops
    """
    tagger = ttw.TreeTagger(TAGLANG=LANGUAGE.lower()[0:2],
                            TAGDIR=CMGR.get_treetagger_path())

    if lex_version == 'base':
        lex = EMOLEX
    elif lex_version == 'enhanced':
        lex = EMOLEX_ENHANCED
    else:
        lex = EMOLEX

    if use_filter:
        analyzer = syuzhet.SyuzhetWithFilter(LANGUAGE, tagger,
                                             EMOTIONS_ARRAY_LENGTH, lex)
    else:
        analyzer = syuzhet.SyuzhetNoFilter(LANGUAGE, tagger,
                                           EMOTIONS_ARRAY_LENGTH, lex)

    analysis_result = analyzer.analyze_text(text,
                                            get_sentences=sent_list,
                                            return_sentence_str=sent_strs,
                                            preprocess=preprocess)

    analyzer = None
    tagger = None

    result = {'aggregate': analysis_result['aggregate'],
              'sentences': analysis_result['sentences']}

    if sent_list:
        result['sentence_list'] = analysis_result['sentence_list']

    if sent_strs:
        result['splitted_sentences'] = analysis_result['sentences_as_str']

    return result


def _convert_result_to_list(data):
    """Convert analysis result to list instead of np.ndarray.
    Utility for postprocessing.
    """

    def ffun(key, value):
        """Mapfun."""
        if key == 'aggregate':
            return value.tolist()
        elif key == 'sentences':
            return [x.tolist() for x in value]

        return value

    return {key: ffun(key, val) for key, val in data}


if __name__ == "__main__":
    APP.run()
