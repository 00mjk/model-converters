import os
import time
import shutil
import socket
import subprocess
import numpy as np
import pytest

from tempfile import NamedTemporaryFile
from tensorflow_serving_client import TensorflowServingClient
from model_converters import KerasToTensorflow
from keras_model_specs import ModelSpec


MODEL_SERVING_PORT = 9001
MODEL_NAME = os.getenv('MODEL_NAME', 'mobilenet_v1')


def assert_lists_same_items(list1, list2):
    assert sorted(list1) == sorted(list2)


def cat_image(model_spec):
    return model_spec.load_image('tests/fixtures/files/cat.jpg')


def setup_model(name, model_path):
    tf_model_dir = '.cache/model'

    model_spec = ModelSpec.get(name)
    model = model_spec.klass(weights='imagenet', input_shape=tuple(model_spec.target_size))
    model_dir = os.path.dirname(model_path)
    if not os.path.exists(model_dir):
        os.makedirs(model_dir)
    model.save(model_path)

    image_data = cat_image(model_spec)
    expected_scores = model.predict(image_data)

    if os.path.exists(tf_model_dir):
        shutil.rmtree(tf_model_dir)
    tf_model_dir_dir = os.path.dirname(tf_model_dir)
    if not os.path.exists(tf_model_dir_dir):
        os.makedirs(tf_model_dir_dir)

    return tf_model_dir, expected_scores


def start_serving_container():
    subprocess.call(['docker-compose', 'up', '-d', 'tf-serving'])
    attempt = 0
    while attempt <= 60:
        attempt += 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(('localhost', MODEL_SERVING_PORT))
            if len(s.recv(1)) > 0:
                break
        except socket.error:
            pass
        time.sleep(1)


def kill_serving_container():
    subprocess.call(['docker-compose', 'stop', 'tf-serving'])


def assert_converted_model(tf_model_dir):
    assert os.path.exists(tf_model_dir)
    assert os.path.exists(tf_model_dir + '/variables')
    assert os.path.exists(tf_model_dir + '/variables/variables.data-00000-of-00001')
    assert os.path.exists(tf_model_dir + '/variables/variables.index')
    assert os.path.exists(tf_model_dir + '/saved_model.pb')


def assert_model_serving(model_name, expected_scores, check_features=False):
    model_spec = ModelSpec.get(model_name)
    client = TensorflowServingClient('localhost', MODEL_SERVING_PORT)
    result = client.make_prediction(cat_image(model_spec), 'image')

    assert 'class_probabilities' in result
    assert len(result['class_probabilities']) == 1

    scores = result['class_probabilities'][0]
    np.testing.assert_array_almost_equal(np.array(scores), np.array(expected_scores).flatten())

    if check_features:
        assert 'image_features' in result
        assert result['image_features'].shape == (1, 1024)


def test_converted_model_has_same_scores():
    with NamedTemporaryFile() as f:
        temp_file = f.name
        tf_model_dir, expected_scores = setup_model(MODEL_NAME, temp_file)
        KerasToTensorflow.convert(temp_file, tf_model_dir)

        assert_converted_model(tf_model_dir)
        start_serving_container()
        assert_model_serving(MODEL_NAME, expected_scores)
        kill_serving_container()


@pytest.mark.skipif(MODEL_NAME != 'mobilenet_v1', reason='we only test image_features on mobilenet')
def test_converted_model_image_features():
    with NamedTemporaryFile() as f:
        temp_file = f.name
        tf_model_dir, expected_scores = setup_model(MODEL_NAME, temp_file)
        KerasToTensorflow.convert(temp_file, tf_model_dir, feature_layer=-6)

        assert_converted_model(tf_model_dir)
        start_serving_container()
        assert_model_serving(MODEL_NAME, expected_scores, check_features=True)
        kill_serving_container()
