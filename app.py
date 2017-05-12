from flask import Flask, send_from_directory
import treetaggerwrapper as ttw
import pickle

import syuzhet
from path_problem_resolver import get_absolute_path
from configuration_manager import ConfigurationManager
# import pudb

cmgr = ConfigurationManager("config.json")
cmgr.load_config()

language = cmgr.get_default_language()
emotions_array_length = cmgr.get_emotion_array_length()
data_dir = cmgr.get_data_dir()
emolex_filename = cmgr.get_emolex_filename(language)

# pudb.set_trace()
emolex_abs_path = get_absolute_path('syuzhet/'
                                    + data_dir + '/' + emolex_filename)

with open(emolex_abs_path, 'rb') as f:
    emolex = pickle.load(f)

tagger = ttw.TreeTagger(TAGLANG=language.lower()[0:2],
                        TAGDIR=cmgr.get_treetagger_path())

analyzer = syuzhet.Syuzhet(language, tagger, emotions_array_length, emolex)


# Main application
app = Flask(__name__)


@app.route('/', methods=['GET'])
def show_readme():
    """Show the help readme."""
    return app.send_static_file('Readme.html')
    # return send_from_directory('static', 'Readme.html')
