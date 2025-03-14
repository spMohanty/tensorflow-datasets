# coding=utf-8
# Copyright 2022 The TensorFlow Datasets Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for tensorflow_datasets.core.dataset_builder."""

import dataclasses
import os
import tempfile
from unittest import mock

from absl.testing import parameterized

import dill
import numpy as np
import tensorflow as tf
from tensorflow_datasets import testing
from tensorflow_datasets.core import constants
from tensorflow_datasets.core import dataset_builder
from tensorflow_datasets.core import dataset_info
from tensorflow_datasets.core import dataset_utils
from tensorflow_datasets.core import download
from tensorflow_datasets.core import features
from tensorflow_datasets.core import load
from tensorflow_datasets.core import splits as splits_lib
from tensorflow_datasets.core import utils
from tensorflow_datasets.core.utils import file_utils
from tensorflow_datasets.core.utils import read_config as read_config_lib

DummyDatasetSharedGenerator = testing.DummyDatasetSharedGenerator


@dataclasses.dataclass
class DummyBuilderConfig(dataset_builder.BuilderConfig):
  increment: int = 0


class DummyDatasetWithConfigs(dataset_builder.GeneratorBasedBuilder):

  BUILDER_CONFIGS = [
      DummyBuilderConfig(
          name="plus1",
          version=utils.Version("0.0.1"),
          description="Add 1 to the records",
          increment=1),
      DummyBuilderConfig(
          name="plus2",
          version=utils.Version("0.0.2"),
          supported_versions=[utils.Version("0.0.1")],
          description="Add 2 to the records",
          increment=2),
  ]

  def _info(self):

    return dataset_info.DatasetInfo(
        builder=self,
        features=features.FeaturesDict({"x": tf.int64}),
        supervised_keys=("x", "x"),
    )

  def _split_generators(self, dl_manager):
    del dl_manager
    return {
        "train": self._generate_examples(range(20)),
        "test": self._generate_examples(range(20, 30)),
    }

  def _generate_examples(self, range_):
    for i in range_:
      x = i
      if self.builder_config:
        x += self.builder_config.increment
      yield i, {"x": x}


class DummyDatasetWithDefaultConfig(DummyDatasetWithConfigs):
  DEFAULT_BUILDER_CONFIG_NAME = "plus2"


class InvalidSplitDataset(DummyDatasetWithConfigs):

  def _split_generators(self, _):
    # Error: ALL cannot be used as Split key
    return {"all": self._generate_examples(range(5))}


class DatasetBuilderTest(testing.TestCase):

  @classmethod
  def setUpClass(cls):
    super(DatasetBuilderTest, cls).setUpClass()
    cls.builder = DummyDatasetSharedGenerator(
        data_dir=os.path.join(tempfile.gettempdir(), "tfds"))
    cls.builder.download_and_prepare()

  @testing.run_in_graph_and_eager_modes()
  def test_load(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      dataset = load.load(
          name="dummy_dataset_with_configs",
          data_dir=tmp_dir,
          download=True,
          split=splits_lib.Split.TRAIN)
      data = list(dataset_utils.as_numpy(dataset))
      self.assertEqual(20, len(data))
      self.assertLess(data[0]["x"], 30)

  # Disable test until dependency on Riegeli is fixed.
  # @testing.run_in_graph_and_eager_modes()
  # def test_load_with_specified_format(self):
  #   with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
  #     dataset, ds_info = load.load(
  #         name="dummy_dataset_with_configs",
  #         with_info=True,
  #         data_dir=tmp_dir,
  #         download=True,
  #         split=splits_lib.Split.TRAIN,
  #         download_and_prepare_kwargs={"file_format": "riegeli"})
  #     self.assertEqual(ds_info.file_format.name, "RIEGELI")
  #     files = tf.io.gfile.listdir(
  #         os.path.join(tmp_dir, "dummy_dataset_with_configs",
  #                      "plus1", "0.0.1"))
  #     self.assertSetEqual(
  #         set(files), {
  #             "dummy_dataset_with_configs-test.riegeli-00000-of-00001",
  #             "dummy_dataset_with_configs-test.riegeli-00000-of-00001_index.json",
  #             "dummy_dataset_with_configs-train.riegeli-00000-of-00001",
  #             "dummy_dataset_with_configs-train.riegeli-00000-of-00001_index.json",
  #             "features.json",
  #             "dataset_info.json",
  #         })
  #     data = list(dataset_utils.as_numpy(dataset))
  #     self.assertEqual(20, len(data))
  #     self.assertLess(data[0]["x"], 30)

  @testing.run_in_graph_and_eager_modes()
  def test_determinism(self):
    ds = self.builder.as_dataset(
        split=splits_lib.Split.TRAIN, shuffle_files=False)
    ds_values = list(dataset_utils.as_numpy(ds))

    # Ensure determinism. If this test fail, this mean that numpy random
    # module isn't always determinist (maybe between version, architecture,
    # ...), and so our datasets aren't guaranteed either.
    l = list(range(20))
    np.random.RandomState(42).shuffle(l)
    self.assertEqual(
        l,
        [0, 17, 15, 1, 8, 5, 11, 3, 18, 16, 13, 2, 9, 19, 4, 12, 7, 10, 14, 6])

    # Ensure determinism. If this test fails, this mean the dataset are not
    # deterministically generated.
    self.assertEqual(
        [e["x"] for e in ds_values],
        [6, 16, 19, 12, 14, 18, 5, 13, 15, 4, 10, 17, 0, 8, 3, 1, 9, 7, 11, 2],
    )

  @testing.run_in_graph_and_eager_modes()
  def test_load_from_gcs(self):
    from tensorflow_datasets.image_classification import mnist  # pylint:disable=import-outside-toplevel,g-import-not-at-top
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      with mock.patch.object(
          mnist.MNIST, "_download_and_prepare",
          side_effect=NotImplementedError):
        # Make sure the dataset cannot be generated.
        with self.assertRaises(NotImplementedError):
          load.load(name="mnist", data_dir=tmp_dir)
        # Enable GCS access so that dataset will be loaded from GCS.
        with self.gcs_access():
          _, info = load.load(name="mnist", data_dir=tmp_dir, with_info=True)
      self.assertSetEqual(
          set([
              "dataset_info.json",
              "image.image.json",
              "mnist-test.tfrecord-00000-of-00001",
              "mnist-train.tfrecord-00000-of-00001",
          ]), set(tf.io.gfile.listdir(os.path.join(tmp_dir, "mnist/3.0.1"))))

      self.assertEqual(set(info.splits.keys()), set(["train", "test"]))

  @testing.run_in_graph_and_eager_modes()
  def test_multi_split(self):
    ds_train, ds_test = self.builder.as_dataset(
        split=["train", "test"], shuffle_files=False)

    data = list(dataset_utils.as_numpy(ds_train))
    self.assertEqual(20, len(data))

    data = list(dataset_utils.as_numpy(ds_test))
    self.assertEqual(10, len(data))

  def test_build_data_dir(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = DummyDatasetSharedGenerator(data_dir=tmp_dir)
      self.assertEqual(str(builder.info.version), "1.0.0")
      builder_data_dir = os.path.join(tmp_dir, builder.name)
      version_dir = os.path.join(builder_data_dir, "1.0.0")

      # The dataset folder contains multiple other versions
      tf.io.gfile.makedirs(os.path.join(builder_data_dir, "14.0.0.invalid"))
      tf.io.gfile.makedirs(os.path.join(builder_data_dir, "10.0.0"))
      tf.io.gfile.makedirs(os.path.join(builder_data_dir, "9.0.0"))
      tf.io.gfile.makedirs(os.path.join(builder_data_dir, "0.1.0"))

      # The builder's version dir is chosen
      self.assertEqual(builder._build_data_dir(tmp_dir)[1], version_dir)

  def test_get_data_dir_with_config(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      config_name = "plus1"
      builder = DummyDatasetWithConfigs(config=config_name, data_dir=tmp_dir)

      builder_data_dir = os.path.join(tmp_dir, builder.name, config_name)
      version_data_dir = os.path.join(builder_data_dir, "0.0.1")

      tf.io.gfile.makedirs(version_data_dir)
      self.assertEqual(builder._build_data_dir(tmp_dir)[1], version_data_dir)

  def test_config_construction(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      self.assertSetEqual(
          set(["plus1", "plus2"]),
          set(DummyDatasetWithConfigs.builder_configs.keys()))
      plus1_config = DummyDatasetWithConfigs.builder_configs["plus1"]
      builder = DummyDatasetWithConfigs(config="plus1", data_dir=tmp_dir)
      self.assertIs(plus1_config, builder.builder_config)
      builder = DummyDatasetWithConfigs(config=plus1_config, data_dir=tmp_dir)
      self.assertIs(plus1_config, builder.builder_config)
      self.assertIs(builder.builder_config,
                    DummyDatasetWithConfigs.default_builder_config)

  @testing.run_in_graph_and_eager_modes()
  def test_with_configs(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder1 = DummyDatasetWithConfigs(config="plus1", data_dir=tmp_dir)
      builder2 = DummyDatasetWithConfigs(config="plus2", data_dir=tmp_dir)
      # Test that builder.builder_config is the correct config
      self.assertIs(builder1.builder_config,
                    DummyDatasetWithConfigs.builder_configs["plus1"])
      self.assertIs(builder2.builder_config,
                    DummyDatasetWithConfigs.builder_configs["plus2"])
      builder1.download_and_prepare()
      builder2.download_and_prepare()
      data_dir1 = os.path.join(tmp_dir, builder1.name, "plus1", "0.0.1")
      data_dir2 = os.path.join(tmp_dir, builder2.name, "plus2", "0.0.2")
      # Test that subdirectories were created per config
      self.assertTrue(tf.io.gfile.exists(data_dir1))
      self.assertTrue(tf.io.gfile.exists(data_dir2))
      # 1 train shard, 1 test shard, plus metadata files
      self.assertGreater(len(tf.io.gfile.listdir(data_dir1)), 2)
      self.assertGreater(len(tf.io.gfile.listdir(data_dir2)), 2)

      # Test that the config was used and they didn't collide.
      splits_list = ["train", "test"]
      for builder, incr in [(builder1, 1), (builder2, 2)]:
        train_data, test_data = [  # pylint: disable=g-complex-comprehension
            [
                el["x"] for el in  # pylint: disable=g-complex-comprehension
                dataset_utils.as_numpy(builder.as_dataset(split=split))
            ] for split in splits_list
        ]

        self.assertEqual(20, len(train_data))
        self.assertEqual(10, len(test_data))
        self.assertCountEqual([incr + el for el in range(30)],
                              train_data + test_data)

  def test_default_builder_config(self):
    self.assertEqual(DummyDatasetWithConfigs.default_builder_config.name,
                     "plus1")
    self.assertEqual(DummyDatasetWithDefaultConfig.default_builder_config.name,
                     "plus2")

  def test_read_config(self):
    is_called = []

    def interleave_sort(lists):
      is_called.append(True)
      return lists

    read_config = read_config_lib.ReadConfig(
        experimental_interleave_sort_fn=interleave_sort,)
    read_config.options.experimental_slack = True
    ds = self.builder.as_dataset(
        split="train",
        read_config=read_config,
        shuffle_files=True,
    )

    # Check that the ReadConfig options are properly set
    self.assertTrue(ds.options().experimental_slack)

    # The instruction function should have been called
    self.assertEqual(is_called, [True])

  def test_with_supported_version(self):
    DummyDatasetWithConfigs(config="plus1", version="0.0.1")

  def test_latest_experimental_version(self):
    builder1 = DummyDatasetSharedGenerator()
    self.assertEqual(str(builder1._version), "1.0.0")
    builder2 = DummyDatasetSharedGenerator(version="experimental_latest")
    self.assertEqual(str(builder2._version), "2.0.0")

  def test_with_unsupported_version(self):
    expected = "Dataset dummy_dataset_with_configs cannot be loaded at version"
    with self.assertRaisesWithPredicateMatch(AssertionError, expected):
      DummyDatasetWithConfigs(config="plus1", version="0.0.2")
    with self.assertRaisesWithPredicateMatch(AssertionError, expected):
      DummyDatasetWithConfigs(config="plus1", version="0.1.*")

  def test_previous_supported_version(self):
    default_builder = DummyDatasetSharedGenerator()
    self.assertEqual(str(default_builder.info.version), "1.0.0")
    older_builder = DummyDatasetSharedGenerator(version="0.0.*")
    self.assertEqual(str(older_builder.info.version), "0.0.9")

  def test_generate_old_versions(self):

    class MultiVersionDataset(dataset_builder.GeneratorBasedBuilder):

      VERSION = utils.Version("1.0.0")
      SUPPORTED_VERSIONS = [
          utils.Version("2.0.0"),
          utils.Version("1.9.0"),  # Cannot be generated
          utils.Version("0.0.8"),  # Cannot be generated
      ]

      def _info(self):
        return dataset_info.DatasetInfo(builder=self)

      def _split_generators(self, dl_manager):
        return []

      def _generate_examples(self):
        yield "", {}

    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = MultiVersionDataset(version="0.0.8", data_dir=tmp_dir)
      with self.assertRaisesWithPredicateMatch(ValueError, "0.0.8) is too old"):
        builder.download_and_prepare()

    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = MultiVersionDataset(version="1.9.0", data_dir=tmp_dir)
      with self.assertRaisesWithPredicateMatch(ValueError, "1.9.0) is too old"):
        builder.download_and_prepare()

    # `experimental_latest` version can be installed
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = MultiVersionDataset(version="2.0.0", data_dir=tmp_dir)
      builder.download_and_prepare()

  def test_invalid_split_dataset(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      with self.assertRaisesWithPredicateMatch(ValueError,
                                               "`all` is a reserved keyword"):
        # Raise error during .download_and_prepare()
        load.load(
            name="invalid_split_dataset",
            data_dir=tmp_dir,
        )

  def test_global_version(self):
    global_version = utils.Version("1.0.0")
    global_release_notes = {
        "1.0.0": "Global release",
    }
    config_version = utils.Version("1.1.0")
    config_release_notes = {
        "1.1.0": "Some update",
    }

    class VersionDummyDataset(DummyDatasetWithConfigs):

      BUILDER_CONFIGS = [
          dataset_builder.BuilderConfig(
              name="default",
              description="Add 1 to the records",
          ),
          dataset_builder.BuilderConfig(
              name="custom",
              description="Add 2 to the records",
              version=config_version,
              release_notes=config_release_notes,
          ),
      ]
      VERSION = global_version
      RELEASE_NOTES = global_release_notes

    tmp_path = "/tmp/non-existing-dir/"

    # If version is not specified at the BuilderConfig level, then
    # the default global values are used.
    builder = VersionDummyDataset(config="default", data_dir=tmp_path)
    self.assertEqual(builder.version, global_version)
    self.assertEqual(builder.release_notes, global_release_notes)

    builder = VersionDummyDataset(config="custom", data_dir=tmp_path)
    self.assertEqual(builder.version, config_version)
    self.assertEqual(builder.release_notes, config_release_notes)


class DatasetBuilderMultiDirTest(testing.TestCase):
  """Tests for multi-dir."""

  @classmethod
  def setUpClass(cls):
    super(DatasetBuilderMultiDirTest, cls).setUpClass()
    cls.builder = DummyDatasetSharedGenerator()
    cls.version_dir = os.path.normpath(cls.builder.info.full_name)

  def setUp(self):
    super(DatasetBuilderMultiDirTest, self).setUp()
    # Sanity check to make sure that no dir is registered
    self.assertEmpty(file_utils._registered_data_dir)
    # Create a new temp dir
    self.other_data_dir = os.path.join(self.get_temp_dir(), "other_dir")
    # Overwrite the default data_dir (as files get created)
    self._original_data_dir = constants.DATA_DIR
    constants.DATA_DIR = os.path.join(self.get_temp_dir(), "default_dir")
    self.default_data_dir = constants.DATA_DIR

  def tearDown(self):
    super(DatasetBuilderMultiDirTest, self).tearDown()
    # Restore to the default `_registered_data_dir`
    file_utils._registered_data_dir = set()
    # Clear-up existing dirs
    if tf.io.gfile.exists(self.other_data_dir):
      tf.io.gfile.rmtree(self.other_data_dir)
    if tf.io.gfile.exists(self.default_data_dir):
      tf.io.gfile.rmtree(self.default_data_dir)
    # Restore the orgininal data dir
    constants.DATA_DIR = self._original_data_dir

  def assertBuildDataDir(self, build_data_dir_out, root_dir):
    data_dir_root, data_dir = build_data_dir_out
    self.assertEqual(data_dir_root, root_dir)
    self.assertEqual(data_dir, os.path.join(root_dir, self.version_dir))

  def test_default(self):
    # No data_dir is passed
    # -> use default path is used.
    self.assertBuildDataDir(
        self.builder._build_data_dir(None), self.default_data_dir)

  def test_explicitly_passed(self):
    # When a dir is explictly passed, use it.
    self.assertBuildDataDir(
        self.builder._build_data_dir(self.other_data_dir), self.other_data_dir)

  def test_default_multi_dir(self):
    # No data_dir is passed
    # Multiple data_dirs are registered
    # -> use default path
    file_utils.add_data_dir(self.other_data_dir)
    self.assertBuildDataDir(
        self.builder._build_data_dir(None), self.default_data_dir)

  def test_default_multi_dir_old_version_exists(self):
    # No data_dir is passed
    # Multiple data_dirs are registered
    # Data dir contains old versions
    # -> use default path
    file_utils.add_data_dir(self.other_data_dir)
    tf.io.gfile.makedirs(
        os.path.join(self.other_data_dir, "dummy_dataset_shared_generator",
                     "0.1.0"))
    tf.io.gfile.makedirs(
        os.path.join(self.other_data_dir, "dummy_dataset_shared_generator",
                     "0.2.0"))
    self.assertBuildDataDir(
        self.builder._build_data_dir(None), self.default_data_dir)

  def test_default_multi_dir_version_exists(self):
    # No data_dir is passed
    # Multiple data_dirs are registered
    # Data found
    # -> Re-load existing data
    file_utils.add_data_dir(self.other_data_dir)
    tf.io.gfile.makedirs(
        os.path.join(self.other_data_dir, "dummy_dataset_shared_generator",
                     "1.0.0"))
    self.assertBuildDataDir(
        self.builder._build_data_dir(None), self.other_data_dir)

  def test_default_multi_dir_duplicate(self):
    # If two data dirs contains the dataset, raise an error...
    file_utils.add_data_dir(self.other_data_dir)
    tf.io.gfile.makedirs(
        os.path.join(self.default_data_dir, "dummy_dataset_shared_generator",
                     "1.0.0"))
    tf.io.gfile.makedirs(
        os.path.join(self.other_data_dir, "dummy_dataset_shared_generator",
                     "1.0.0"))
    with self.assertRaisesRegex(ValueError, "found in more than one directory"):
      self.builder._build_data_dir(None)

  def test_expicit_multi_dir(self):
    # If two data dirs contains the same version
    # Data dir is explicitly passed
    file_utils.add_data_dir(self.other_data_dir)
    tf.io.gfile.makedirs(
        os.path.join(self.default_data_dir, "dummy_dataset_shared_generator",
                     "1.0.0"))
    tf.io.gfile.makedirs(
        os.path.join(self.other_data_dir, "dummy_dataset_shared_generator",
                     "1.0.0"))
    self.assertBuildDataDir(
        self.builder._build_data_dir(self.other_data_dir), self.other_data_dir)

  def test_load_data_dir(self):
    """Ensure that `tfds.load` also supports multiple data_dir."""
    file_utils.add_data_dir(self.other_data_dir)

    class MultiDirDataset(DummyDatasetSharedGenerator):  # pylint: disable=unused-variable
      VERSION = utils.Version("1.2.0")

    data_dir = os.path.join(self.other_data_dir, "multi_dir_dataset", "1.2.0")
    tf.io.gfile.makedirs(data_dir)

    with mock.patch.object(dataset_info.DatasetInfo, "read_from_directory"):
      _, info = load.load("multi_dir_dataset", split=[], with_info=True)
    self.assertEqual(info.data_dir, data_dir)


class DummyOrderedDataset(dataset_builder.GeneratorBasedBuilder):
  VERSION = utils.Version("1.0.0")

  def _info(self):
    return dataset_info.DatasetInfo(
        builder=self,
        features=features.FeaturesDict({"x": tf.int64}),
        disable_shuffling=True,
    )

  def _split_generators(self, dl_manager):
    del dl_manager
    return {"train": self._generate_examples(range_=range(500))}

  def _generate_examples(self, range_):
    for i in range_:
      yield i, {"x": i}


class OrderedDatasetBuilderTest(testing.TestCase):

  @classmethod
  def setUpClass(cls):
    super(OrderedDatasetBuilderTest, cls).setUpClass()
    with mock.patch("tensorflow_datasets.core.writer._get_number_shards",
                    lambda x, y: 10):
      cls.builder = DummyOrderedDataset(
          data_dir=os.path.join(tempfile.gettempdir(), "tfds"))
      cls.builder.download_and_prepare()

  @testing.run_in_graph_and_eager_modes()
  def test_sorted_by_key(self):
    # For ordered dataset ReadConfig.interleave_cycle_length=1 by default
    read_config = read_config_lib.ReadConfig()
    ds = self.builder.as_dataset(
        split=splits_lib.Split.TRAIN,
        shuffle_files=False,
        read_config=read_config)
    ds_values = list(dataset_utils.as_numpy(ds))
    self.assertListEqual(self.builder.info.splits["train"].shard_lengths,
                         [50] * 10)
    self.assertEqual(
        [e["x"] for e in ds_values],
        list(range(500)),
    )


class BuilderPickleTest(testing.TestCase):

  def test_load_dump(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = testing.DummyMnist(data_dir=tmp_dir)
    builder2 = dill.loads(dill.dumps(builder))
    self.assertEqual(builder.name, builder2.name)
    self.assertEqual(builder.version, builder2.version)


class BuilderRestoreGcsTest(testing.TestCase):

  def setUp(self):
    super(BuilderRestoreGcsTest, self).setUp()

    def load_mnist_dataset_info(self):
      mnist_info_path = os.path.join(
          utils.tfds_path(),
          "testing/test_data/dataset_info/mnist/3.0.1",
      )
      mnist_info_path = os.path.normpath(mnist_info_path)
      self.read_from_directory(mnist_info_path)

    patcher = mock.patch.object(
        dataset_info.DatasetInfo,
        "initialize_from_bucket",
        new=load_mnist_dataset_info)
    patcher.start()
    self.patch_gcs = patcher
    self.addCleanup(patcher.stop)

  def test_stats_restored_from_gcs(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = testing.DummyMnist(data_dir=tmp_dir)
      self.assertEqual(builder.info.splits["train"].num_examples, 20)

  def test_stats_not_restored_gcs_overwritten(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      # If split are different that the one restored, stats should be recomputed
      builder = testing.DummyMnist(data_dir=tmp_dir)
      self.assertEqual(builder.info.splits["train"].num_examples, 20)

  def test_gcs_not_exists(self):
    # By disabling the patch, and because DummyMnist is not on GCS, we can
    # simulate a new dataset starting from scratch
    self.patch_gcs.stop()
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = testing.DummyMnist(data_dir=tmp_dir)
      # No dataset_info restored, so stats are empty
      self.assertEqual(builder.info.splits.total_num_examples, 0)

      dl_config = download.DownloadConfig()
      builder.download_and_prepare(download_config=dl_config)

      # Statistics should have been recomputed
      self.assertEqual(builder.info.splits["train"].num_examples, 20)
    self.patch_gcs.start()


class DatasetBuilderGenerateModeTest(testing.TestCase):

  @classmethod
  def setUpClass(cls):
    super(DatasetBuilderGenerateModeTest, cls).setUpClass()

  def test_reuse_cache_if_exists(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      builder = testing.DummyMnist(data_dir=tmp_dir)
      dl_config = download.DownloadConfig(max_examples_per_split=3)
      builder.download_and_prepare(download_config=dl_config)

      dl_config = download.DownloadConfig(
          download_mode=download.GenerateMode.REUSE_CACHE_IF_EXISTS,
          max_examples_per_split=5)
      builder.download_and_prepare(download_config=dl_config)
      self.assertEqual(builder.info.splits["train"].num_examples, 5)


class DatasetBuilderReadTest(testing.TestCase):

  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    cls._tfds_tmp_dir = testing.make_tmp_dir()
    builder = DummyDatasetSharedGenerator(data_dir=cls._tfds_tmp_dir)
    builder.download_and_prepare()

  @classmethod
  def tearDownClass(cls):
    super().tearDownClass()
    testing.rm_tmp_dir(cls._tfds_tmp_dir)

  def setUp(self):
    super(DatasetBuilderReadTest, self).setUp()
    self.builder = DummyDatasetSharedGenerator(data_dir=self._tfds_tmp_dir)

  @testing.run_in_graph_and_eager_modes()
  def test_all_splits(self):
    splits = dataset_utils.as_numpy(self.builder.as_dataset(batch_size=-1))
    self.assertSetEqual(
        set(splits.keys()), set([splits_lib.Split.TRAIN,
                                 splits_lib.Split.TEST]))

    # Test that enum and string both access same object
    self.assertIs(splits["train"], splits[splits_lib.Split.TRAIN])
    self.assertIs(splits["test"], splits[splits_lib.Split.TEST])

    train_data = splits[splits_lib.Split.TRAIN]["x"]
    test_data = splits[splits_lib.Split.TEST]["x"]
    self.assertEqual(20, len(train_data))
    self.assertEqual(10, len(test_data))
    self.assertEqual(sum(range(30)), int(train_data.sum() + test_data.sum()))

  @testing.run_in_graph_and_eager_modes()
  def test_with_batch_size(self):
    items = list(
        dataset_utils.as_numpy(
            self.builder.as_dataset(split="train+test", batch_size=10)))
    # 3 batches of 10
    self.assertEqual(3, len(items))
    x1, x2, x3 = items[0]["x"], items[1]["x"], items[2]["x"]
    self.assertEqual(10, x1.shape[0])
    self.assertEqual(10, x2.shape[0])
    self.assertEqual(10, x3.shape[0])
    self.assertEqual(sum(range(30)), int(x1.sum() + x2.sum() + x3.sum()))

    # By default batch_size is None and won't add a batch dimension
    ds = self.builder.as_dataset(split=splits_lib.Split.TRAIN)
    self.assertEqual(0, len(tf.compat.v1.data.get_output_shapes(ds)["x"]))
    # Setting batch_size=1 will add an extra batch dimension
    ds = self.builder.as_dataset(split=splits_lib.Split.TRAIN, batch_size=1)
    self.assertEqual(1, len(tf.compat.v1.data.get_output_shapes(ds)["x"]))
    # Setting batch_size=2 will add an extra batch dimension
    ds = self.builder.as_dataset(split=splits_lib.Split.TRAIN, batch_size=2)
    self.assertEqual(1, len(tf.compat.v1.data.get_output_shapes(ds)["x"]))

  def test_autocache(self):
    # All the following should cache

    # Default should cache as dataset is small and has a single shard
    self.assertTrue(
        self.builder._should_cache_ds(
            split="train",
            shuffle_files=True,
            read_config=read_config_lib.ReadConfig(),
        ))

    # Multiple shards should cache when shuffling is disabled
    self.assertTrue(
        self.builder._should_cache_ds(
            split="train+test",
            shuffle_files=False,
            read_config=read_config_lib.ReadConfig(),
        ))

    # Multiple shards should cache when re-shuffling is disabled
    self.assertTrue(
        self.builder._should_cache_ds(
            split="train+test",
            shuffle_files=True,
            read_config=read_config_lib.ReadConfig(
                shuffle_reshuffle_each_iteration=False),
        ))

    # Sub-split API can cache if only a single shard is selected.
    self.assertTrue(
        self.builder._should_cache_ds(
            split="train+test[:0]",
            shuffle_files=True,
            read_config=read_config_lib.ReadConfig(),
        ))

    # All the following should NOT cache

    # Default should not cache if try_autocache is disabled
    self.assertFalse(
        self.builder._should_cache_ds(
            split="train",
            shuffle_files=True,
            read_config=read_config_lib.ReadConfig(try_autocache=False),
        ))

    # Multiple shards should not cache when shuffling is enabled
    self.assertFalse(
        self.builder._should_cache_ds(
            split="train+test",
            shuffle_files=True,
            read_config=read_config_lib.ReadConfig(),
        ))

  def test_with_tfds_info(self):
    ds = self.builder.as_dataset(split=splits_lib.Split.TRAIN)
    self.assertEqual(0, len(tf.compat.v1.data.get_output_shapes(ds)["x"]))


class DummyDatasetWithSupervisedKeys(DummyDatasetSharedGenerator):

  def __init__(self, *args, supervised_keys=None, **kwargs):
    self.supervised_keys = supervised_keys
    super().__init__(*args, **kwargs)

  def _info(self):
    return dataset_info.DatasetInfo(
        builder=self,
        features=features.FeaturesDict({"x": tf.int64}),
        supervised_keys=self.supervised_keys,
    )


class DatasetBuilderAsSupervisedTest(parameterized.TestCase, testing.TestCase):

  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    cls._tfds_tmp_dir = testing.make_tmp_dir()
    builder = DummyDatasetWithSupervisedKeys(data_dir=cls._tfds_tmp_dir)
    builder.download_and_prepare()

  @classmethod
  def tearDownClass(cls):
    super().tearDownClass()
    testing.rm_tmp_dir(cls._tfds_tmp_dir)

  @testing.run_in_graph_and_eager_modes()
  def test_supervised_keys_basic(self):
    self.builder = DummyDatasetWithSupervisedKeys(
        data_dir=self._tfds_tmp_dir, supervised_keys=("x", "x"))
    x, _ = dataset_utils.as_numpy(
        self.builder.as_dataset(
            split=splits_lib.Split.TRAIN, as_supervised=True, batch_size=-1))
    self.assertEqual(x.shape[0], 20)

  def test_supervised_keys_triple(self):
    self.builder = DummyDatasetWithSupervisedKeys(
        data_dir=self._tfds_tmp_dir, supervised_keys=("x", "x", "x"))
    result = dataset_utils.as_numpy(
        self.builder.as_dataset(
            split=splits_lib.Split.TRAIN, as_supervised=True, batch_size=-1))
    self.assertLen(result, 3)
    self.assertEqual(result[0].shape[0], 20)

  def test_supervised_keys_nested(self):
    self.builder = DummyDatasetWithSupervisedKeys(
        data_dir=self._tfds_tmp_dir,
        supervised_keys=("x", ("x", ("x", "x")), {
            "a": "x",
            "b": ("x",)
        }))
    single, pair, a_dict = dataset_utils.as_numpy(
        self.builder.as_dataset(
            split=splits_lib.Split.TRAIN, as_supervised=True, batch_size=-1))
    self.assertEqual(single.shape[0], 20)
    self.assertLen(pair, 2)
    self.assertEqual(pair[1][1].shape[0], 20)
    self.assertLen(a_dict, 2)
    self.assertEqual(a_dict["b"][0].shape[0], 20)

  @parameterized.named_parameters(
      ("not_a_tuple", "x", "tuple of 2 or 3"),
      ("wrong_length_tuple", ("x", "x", "x", "x", "x"), "tuple of 2 or 3"),
      ("wrong_nested_type", ("x", ["x", "x"]), "tuple, dict, str"),
  )
  def test_bad_supervised_keys(self, supervised_keys, error_message):
    with self.assertRaisesRegex(ValueError, error_message):
      self.builder = DummyDatasetWithSupervisedKeys(
          data_dir=self._tfds_tmp_dir,
          # Not a tuple
          supervised_keys=supervised_keys)




class NestedSequenceBuilder(dataset_builder.GeneratorBasedBuilder):
  """Dataset containing nested sequences."""

  VERSION = utils.Version("0.0.1")

  def _info(self):
    return dataset_info.DatasetInfo(
        builder=self,
        features=features.FeaturesDict({
            "frames":
                features.Sequence({
                    "coordinates":
                        features.Sequence(
                            features.Tensor(shape=(2,), dtype=tf.int32)),
                }),
        }),
    )

  def _split_generators(self, dl_manager):
    del dl_manager
    return {"train": self._generate_examples()}

  def _generate_examples(self):
    ex0 = [[[0, 1], [2, 3], [4, 5]], [], [[6, 7]]]
    ex1 = []
    ex2 = [
        [[10, 11]],
        [[12, 13], [14, 15]],
    ]
    for i, ex in enumerate([ex0, ex1, ex2]):
      yield i, {"frames": {"coordinates": ex}}


class NestedSequenceBuilderTest(testing.TestCase):
  """Test of the NestedSequenceBuilder."""

  @classmethod
  def setUpClass(cls):
    super(NestedSequenceBuilderTest, cls).setUpClass()
    dataset_builder._is_py2_download_and_prepare_disabled = False

  @classmethod
  def tearDownClass(cls):
    dataset_builder._is_py2_download_and_prepare_disabled = True
    super(NestedSequenceBuilderTest, cls).tearDownClass()

  @testing.run_in_graph_and_eager_modes()
  def test_nested_sequence(self):
    with testing.tmp_dir(self.get_temp_dir()) as tmp_dir:
      ds_train, ds_info = load.load(
          name="nested_sequence_builder",
          data_dir=tmp_dir,
          split="train",
          with_info=True,
          shuffle_files=False)
      ex0, ex1, ex2 = [
          ex["frames"]["coordinates"] for ex in dataset_utils.as_numpy(ds_train)
      ]
      self.assertAllEqual(
          ex0,
          tf.ragged.constant([
              [[0, 1], [2, 3], [4, 5]],
              [],
              [[6, 7]],
          ],
                             inner_shape=(2,)))
      self.assertAllEqual(ex1, tf.ragged.constant([], ragged_rank=1))
      self.assertAllEqual(
          ex2,
          tf.ragged.constant([
              [[10, 11]],
              [[12, 13], [14, 15]],
          ],
                             inner_shape=(2,)))

      self.assertEqual(
          ds_info.features.dtype,
          {"frames": {
              "coordinates": tf.int32
          }},
      )
      self.assertEqual(
          ds_info.features.shape,
          {"frames": {
              "coordinates": (None, None, 2)
          }},
      )
      nested_tensor_info = ds_info.features.get_tensor_info()
      self.assertEqual(
          nested_tensor_info["frames"]["coordinates"].sequence_rank,
          2,
      )


if __name__ == "__main__":
  testing.test_main()
