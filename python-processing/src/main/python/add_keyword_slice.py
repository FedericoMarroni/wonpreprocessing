import codecs
import sys
from os import listdir
from os.path import isfile, join

import numpy as np
from scipy.sparse.coo import coo_matrix
from scipy.io import mmwrite

from feature_extraction import vectorize_and_transform, apply_threshold
from evaluate_link_prediction import read_input_tensor

import six


if len(sys.argv) != 3:
    raise Exception("ARGUMENTS: <documents dir> <rescal_dir>")

doc_path = sys.argv[1]
rescal_path = sys.argv[2]

# documents = [f.rstrip('.eml') for f in listdir(doc_path) if
#              isfile(join(doc_path, f)) and f.endswith('.eml')]


# Manual approach for unicode decode errors
documents = []
for f in listdir(doc_path):
    if isfile(join(doc_path, f)) and f.endswith('.eml'):
        try:
            documents.append(six.text_type(f.rstrip('.eml')))
        except UnicodeDecodeError:
            pass


print('Loaded ', len(documents), ' files from path: ', doc_path)

print('Extracting features')

file_paths = [join(doc_path, f + '.eml') for f in documents]
data, features = vectorize_and_transform(file_paths)
data = apply_threshold(data, 0.3)

# TODO: filter features and filenames based on whether their entries survived

print('Reading tensor')
tensor, headers = read_input_tensor(rescal_path)

NEED_STR = 'Need: '
ATTR_STR = 'Attr: '
LEN = len(NEED_STR)

document_index = {}
headers_cursor = 1
for header in headers:
    if header.startswith(NEED_STR):
        document_index[header[LEN:]] = headers_cursor
    headers_cursor += 1

for filename in documents:
    if filename not in document_index:
        # print('Adding NEED at index (', headers_cursor, '): ', filename)
        headers.append(NEED_STR + filename)
        document_index[filename] = headers_cursor
        headers_cursor += 1

feature_index = {}
for feature in features:
    # print('Adding ATTR at index (', headers_cursor, '): ', feature)
    headers.append(ATTR_STR + feature)
    feature_index[feature] = headers_cursor
    headers_cursor += 1

offset_row = np.array([document_index[documents[i]] for i in data.row])
offset_col = np.array([feature_index[features[i]] for i in data.col])

offset_matrix = coo_matrix((data.data, (offset_row, offset_col)))

with codecs.open(rescal_path + '/headers.txt','w',encoding='utf8') as f:
    f.write('\n'.join(headers))

# with open(rescal_path + '/headers.txt', mode='w', encoding='utf-8') as f:
#     f.write('\n'.join(headers))

mmwrite(rescal_path + '/keywords_slice.mtx', offset_matrix)

