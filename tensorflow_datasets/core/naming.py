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

"""Utilities for file names."""

from __future__ import annotations

import dataclasses
import os
import re
import textwrap
from typing import Any, Dict, List, MutableMapping, Optional, Tuple, Union

from etils import epath
from tensorflow_datasets.core.utils import py_utils

_NAME_CLASS = r'[a-zA-Z][\w]*'
_NAME_CLASS_REG = re.compile(r'^' + _NAME_CLASS + r'$')

# Regex matching 'dataset/config:1.*.*/arg=123'
_NAME_REG = re.compile(r'^'
                       r'(?P<dataset_name>([\w\-]+:)?' + _NAME_CLASS + r')'
                       r'(/(?P<config>[\w\-\.]+))?'
                       r'(:(?P<version>(\d+|\*)(\.(\d+|\*)){2}))?'
                       r'(/(?P<kwargs>(\w+=\w+)(,\w+=[^,]+)*))?'
                       r'$')

_DEFAULT_NUM_DIGITS_FOR_SHARDS = 5

_VAR_DATASET = 'DATASET'
_VAR_SPLIT = 'SPLIT'
_VAR_SHARD_INDEX = 'SHARD_INDEX'
_VAR_NUM_SHARDS = 'NUM_SHARDS'
_VAR_SHARD_X_OF_Y = 'SHARD_X_OF_Y'
_VAR_FILEFORMAT = 'FILEFORMAT'
_VAR_REGEX_MAPPING = {
    _VAR_DATASET: rf'(?P<dataset_name>{_NAME_CLASS})',
    _VAR_FILEFORMAT: r'(?P<filetype_suffix>\w+)',
    _VAR_SPLIT: r'(?P<split>(\w|-)+)',
    _VAR_SHARD_INDEX: r'(?P<shard_index>\d{5,})',
    _VAR_NUM_SHARDS: r'(?P<num_shards>\d{5,})',
    _VAR_SHARD_X_OF_Y: r'(?P<shard_index>\d{5,})-of-(?P<num_shards>\d{5,})',
}

DEFAULT_FILENAME_TEMPLATE = '{DATASET}-{SPLIT}.{FILEFORMAT}-{SHARD_X_OF_Y}'

_first_cap_re = re.compile('(.)([A-Z][a-z0-9]+)')
_all_cap_re = re.compile('([a-z0-9])([A-Z])')

Value = Union[str, int, float, bool]


@dataclasses.dataclass(eq=True, order=True, frozen=True)
class DatasetName:
  """Dataset namespace+name."""
  namespace: Optional[str]
  name: str

  def __init__(
      self,
      namespace_name: Optional[str] = None,
      # TODO(py3.8): Positional-only arg /,
      *,
      namespace: Optional[str] = None,
      name: Optional[str] = None,
  ):
    if namespace_name and bool(namespace or name):
      raise ValueError(
          "Name should be defined by `DatasetName('ns:name')` or "
          "`DatasetName(namespace='ns', name='name'). Mixing args and kwargs "
          'is invalid.')
    if namespace_name:
      if ':' in namespace_name:
        namespace, name = namespace_name.split(':')
      else:
        namespace, name = None, namespace_name
    super().__setattr__('namespace', namespace)
    super().__setattr__('name', name)

  def __str__(self) -> str:
    return f'{self.namespace}:{self.name}' if self.namespace else self.name

  def __repr__(self) -> str:
    return f'{type(self).__name__}({str(self)!r})'


def is_valid_dataset_name(name_str: str) -> bool:
  """Returns True is the given name is a valid dataset name."""
  res = _NAME_REG.match(name_str)
  return bool(res)


def is_valid_dataset_and_class_name(name_str: str) -> bool:
  """Returns True is the given name is a valid dataset and class name."""
  res = _NAME_CLASS_REG.match(name_str)
  return bool(res)


def parse_builder_name_kwargs(
    name: str,
    **builder_kwargs: Any,
) -> Tuple[DatasetName, Dict[str, Any]]:
  """Normalize builder kwargs.

  Example:

  ```python
  ds_name, builder_kwargs = parse_builder_name_kwargs(
      'kaggle:ds/cfg:1.2.3', data_dir='...'
  )
  ds_name.namespace == 'kaggle'
  ds_name.name == 'ds'
  builder_kwargs == {'config': 'cfg', 'version': '1.2.3', 'data_dir': '...'}
  ```

  Args:
    name: Name
    **builder_kwargs: Builder kwargs

  Returns:
    ns_name: DatasetName object for the given dataset name
    builder_kwargs: Builder kwargs (version, config, data_dir,...)
  """
  name, parsed_builder_kwargs = _dataset_name_and_kwargs_from_name_str(name)
  builder_kwargs = dict(**parsed_builder_kwargs, **builder_kwargs)
  return DatasetName(name), builder_kwargs


def _dataset_name_and_kwargs_from_name_str(
    name_str: str,) -> Tuple[str, Dict[str, Value]]:
  """Extract kwargs from name str."""
  err_msg = textwrap.dedent(f"""\
      Parsing builder name string {name_str} failed.
      The builder name string must be of the following format:
        dataset_name[/config_name][:version][/kwargs]

        Where:

          * dataset_name and config_name are string following python variable naming.
          * version is of the form x.y.z where {{x,y,z}} can be any digit or *.
          * kwargs is a comma list separated of arguments and values to pass to
            builder.

        Examples:
          my_dataset
          my_dataset:1.2.*
          my_dataset/config1
          my_dataset/config1:1.*.*
          my_dataset/config1/arg1=val1,arg2=val2
          my_dataset/config1:1.2.3/right=True,foo=bar,rate=1.2
      """)

  res = _NAME_REG.match(name_str)
  if not res:
    raise ValueError(err_msg)
  name = res.group('dataset_name')
  # Normalize the name to accept CamelCase
  name = camelcase_to_snakecase(name)
  kwargs = _kwargs_str_to_kwargs(res.group('kwargs'))
  try:
    for attr in ['config', 'version']:
      val = res.group(attr)
      if val is None:
        continue
      if attr in kwargs:
        raise ValueError('Dataset %s: cannot pass %s twice.' % (name, attr))
      kwargs[attr] = val
    return name, kwargs
  except Exception as e:  # pylint: disable=broad-except
    py_utils.reraise(e, prefix=err_msg)  # pytype: disable=bad-return-type


def _kwargs_str_to_kwargs(kwargs_str: str):
  """Converts given `kwargs` as str into kwargs dict."""
  if not kwargs_str:
    return {}
  kwarg_strs = kwargs_str.split(',')
  kwargs = {}
  for kwarg_str in kwarg_strs:
    kwarg_name, kwarg_val = kwarg_str.split('=')
    kwargs[kwarg_name] = _cast_to_pod(kwarg_val)
  return kwargs


def _cast_to_pod(val: str) -> Value:
  """Try cast to bool, int, float, str, in that order."""
  bools = {'True': True, 'False': False}
  if val in bools:
    return bools[val]
  try:
    return int(val)
  except ValueError:
    try:
      return float(val)
    except ValueError:
      return val


def camelcase_to_snakecase(name: str) -> str:
  """Convert camel-case string to snake-case."""
  s1 = _first_cap_re.sub(r'\1_\2', name)
  return _all_cap_re.sub(r'\1_\2', s1).lower()


def snake_to_camelcase(name: str) -> str:
  """Convert snake-case string to camel-case string."""
  return ''.join(n.capitalize() for n in name.split('_'))


def filename_prefix_for_name(name: str) -> str:
  if os.path.basename(name) != name:
    raise ValueError('Should be a dataset name, not a path: %s' % name)
  return camelcase_to_snakecase(name)


def filename_prefix_for_split(name: str, split: str) -> str:
  if os.path.basename(name) != name:
    raise ValueError('Should be a dataset name, not a path: %s' % name)
  return '%s-%s' % (filename_prefix_for_name(name), split)


def _num_digits_needed(num_shards: Optional[int]) -> int:
  return max(len(str(num_shards or 0)), _DEFAULT_NUM_DIGITS_FOR_SHARDS)


def _replace_shard_suffix(filepath: str, replacement: str) -> str:
  """Replaces the shard suffix (must be at the end) with the given string."""
  (new_string, num_subs) = re.subn(
      pattern=r'^(.+?)-?\d{5,}(-of-\d{5,})?$',
      repl=rf'\g<1>{replacement}',
      string=filepath)
  if num_subs != 1:
    raise RuntimeError(
        f'Should do 1 shard suffix substitution, but did {num_subs}! '
        f'Filepath was {filepath}')
  return new_string


def _filename_template_to_regex(filename_template: str) -> str:
  """Returns the regular expression for the given template.

  Arguments:
    filename_template: the filename template to create a regex for.

  Returns:
    the regular expression for the filename template.

  Raises:
    ValueError: when not all variables in the template were substituted.
  """
  result = filename_template.replace('.', r'\.')
  for var, regex in _VAR_REGEX_MAPPING.items():
    result = result.replace(f'{{{var}}}', regex)
  if re.match(re.compile(r'\{\w+\}'), result):
    raise ValueError('Regex still contains variables '
                     f'that have not been substituted: {result}')
  return result


@dataclasses.dataclass()
class ShardedFileTemplate:
  """Template to produce filenames for sharded datasets.

  Attributes:
    data_dir: the directory that contains the files for the shards.
    template: template of the sharded files, e.g.
      '${SPLIT}/data.${FILEFORMAT}-${SHARD_INDEX}'.
    dataset_name: the name of the dataset.
    split: the split of the dataset.
    filetype_suffix: the filetype suffix to denote the type of file. For
      example, `tfrecord`.
  """
  data_dir: epath.Path
  template: str = DEFAULT_FILENAME_TEMPLATE
  dataset_name: Optional[str] = None
  split: Optional[str] = None
  filetype_suffix: Optional[str] = None

  def __post_init__(self):
    self.data_dir = epath.Path(self.data_dir)
    if self.split is not None and not self.split:
      raise ValueError(f'Split must be a non-empty string: {self}')
    if self.filetype_suffix is not None and not self.filetype_suffix:
      raise ValueError(f'Filetype suffix must be a non-empty string: {self}')
    if not self.template:
      self.template = DEFAULT_FILENAME_TEMPLATE

  @py_utils.memoized_property
  def regex(self) -> 're.Pattern[str]':
    """Returns the regular expresssion for this template.

    Can be used to test whether a filename matches to this template.
    """
    return re.compile(_filename_template_to_regex(self.template))

  def parse_filename_info(self, filename: str) -> Optional[FilenameInfo]:
    """Parses the filename using this template.

    Note that when the filename doesn't specify the dataset name, split, or
    filetype suffix, but this template does, then the value in the template will
    be used.

    Arguments:
      filename: the filename that should be parsed.

    Returns:
      the FilenameInfo corresponding to the given file if it could be parsed.
      None otherwise.
    """
    match = self.regex.fullmatch(filename)
    if not match:
      return None
    groupdict = match.groupdict()
    shard_index = groupdict.get('shard_index')
    num_shards = groupdict.get('num_shards')
    return FilenameInfo(
        dataset_name=groupdict.get('dataset_name', self.dataset_name),
        split=groupdict.get('split', self.split),
        filetype_suffix=groupdict.get('filetype_suffix', self.filetype_suffix),
        shard_index=int(shard_index) if shard_index is not None else None,
        num_shards=int(num_shards) if num_shards is not None else None,
        filename_template=self)

  def is_valid(self, filename: str) -> bool:
    """Returns whether the given filename follows this template."""
    filename_info = self.parse_filename_info(filename)
    if filename_info is None:
      return False

    # Even when `dataset_name` is set, it may not be in the template,
    # so also test that `filename_info.dataset_name` is not None`.`
    if (self.dataset_name is not None and
        filename_info.dataset_name is not None and
        filename_info.dataset_name != self.dataset_name):
      return False
    if (self.split is not None and filename_info.split is not None and
        filename_info.split != self.split):
      return False
    if (self.filetype_suffix is not None and
        filename_info.filetype_suffix is not None and
        filename_info.filetype_suffix != self.filetype_suffix):
      return False
    return True

  def _default_mappings(self) -> MutableMapping[str, Any]:
    mappings = {}
    if self.split:
      mappings[_VAR_SPLIT] = self.split
    if self.dataset_name:
      mappings[_VAR_DATASET] = self.dataset_name
    if self.filetype_suffix:
      mappings[_VAR_FILEFORMAT] = self.filetype_suffix
    return mappings

  def relative_filepath(
      self,
      *,
      shard_index: int,
      num_shards: Optional[int],
  ) -> str:
    """Returns the path (relative to the data dir) of the shard."""
    mappings = self._default_mappings()

    # Add shard related information that is formatted consistently
    shard_number_template = '{n:0%d}' % _num_digits_needed(num_shards)
    mappings[_VAR_SHARD_INDEX] = shard_number_template.format(n=shard_index)
    if num_shards:
      mappings[_VAR_NUM_SHARDS] = shard_number_template.format(n=num_shards)
      mappings[_VAR_SHARD_X_OF_Y] = (f'{mappings[_VAR_SHARD_INDEX]}'
                                     f'-of-{mappings[_VAR_NUM_SHARDS]}')
    return self.template.format(**mappings)

  def sharded_filepath(
      self,
      *,
      shard_index: int,
      num_shards: Optional[int],
  ) -> epath.Path:
    """Returns the filename (including full path if `data_dir` is set) for the given shard."""
    return self.data_dir / self.relative_filepath(
        shard_index=shard_index, num_shards=num_shards)

  def sharded_filepaths(
      self,
      num_shards: int,
  ) -> List[epath.Path]:
    return [
        self.sharded_filepath(shard_index=i, num_shards=num_shards)
        for i in range(num_shards)
    ]

  def filepath_prefix(self,) -> str:
    a_filepath = self.sharded_filepath(shard_index=0, num_shards=1)
    return _replace_shard_suffix(os.fspath(a_filepath), '')

  def sharded_filepaths_pattern(
      self,
      *,
      num_shards: Optional[int] = None,
  ) -> str:
    """Returns a pattern describing all the file paths captured by this template.

    If `num_shards` is given, then it returns
    '/path/dataset_name-split.fileformat@num_shards`.
    If `num_shards` is not given, then it returns
    '/path/dataset_name-split.fileformat*`.

    Args:
      num_shards: optional specification of the number of shards.

    Returns:
      the pattern describing all shards captured by this template.
    """
    a_filepath = self.sharded_filepath(shard_index=0, num_shards=1)
    if num_shards:
      replacement = f'@{num_shards}'
    else:
      replacement = '*'
    return _replace_shard_suffix(os.fspath(a_filepath), replacement)

  def sharded_filenames(self, num_shards: int) -> List[str]:
    return [path.name for path in self.sharded_filepaths(num_shards=num_shards)]

  def replace(self, **kwargs: Any) -> 'ShardedFileTemplate':
    """Returns a copy of the `ShardedFileTemplate` with updated attributes."""
    return dataclasses.replace(self, **kwargs)


def filepattern_for_dataset_split(
    *,
    dataset_name: str,
    split: str,
    data_dir: str,
    filetype_suffix: Optional[str] = None,
    num_shards: Optional[int] = None,
) -> str:
  """Returns the file pattern for the given dataset.

  TODO(tfds): remove this by start using ShardedFileTemplate

  Args:
    dataset_name: Name of the dataset
    split: Name of the requested split
    data_dir: The base folder that contains the dataset.
    filetype_suffix: Optional suffix, e.g. tfrecord
    num_shards: Optional argument. If specified, will return file@num_shards
      notation, otherwise file*.
  """
  template = ShardedFileTemplate(
      data_dir=epath.Path(data_dir),
      dataset_name=dataset_name,
      split=split,
      filetype_suffix=filetype_suffix)
  return os.fspath(template.sharded_filepaths_pattern(num_shards=num_shards))


def filenames_for_dataset_split(
    dataset_name: str,
    split: str,
    num_shards: int,
    filetype_suffix: str,
    data_dir: Optional[epath.PathLike] = None,
) -> List[str]:
  """Returns the list of filenames for the given dataset and split."""
  # TODO(tfds): remove this by start using ShardedFileTemplate
  template = ShardedFileTemplate(
      dataset_name=dataset_name,
      split=split,
      filetype_suffix=filetype_suffix,
      data_dir=epath.Path(data_dir))
  return [
      os.fspath(fp) for fp in template.sharded_filenames(num_shards=num_shards)
  ]


def filepaths_for_dataset_split(
    dataset_name: str,
    split: str,
    num_shards: int,
    data_dir: str,
    filetype_suffix: str,
) -> List[str]:
  """File paths of a given dataset split."""
  # TODO(tfds): remove this by start using ShardedFileTemplate
  template = ShardedFileTemplate(
      dataset_name=dataset_name,
      split=split,
      filetype_suffix=filetype_suffix,
      data_dir=epath.Path(data_dir))
  return [
      os.fspath(fp) for fp in template.sharded_filepaths(num_shards=num_shards)
  ]


def _get_filename_template(
    filename: str,
    filename_template: Optional[ShardedFileTemplate]) -> ShardedFileTemplate:
  if filename_template is None:
    return ShardedFileTemplate(data_dir=epath.Path(os.path.dirname(filename)))
  return filename_template


@dataclasses.dataclass(eq=True, frozen=True)
class FilenameInfo:
  """Structure representing a filename.

  Attributes:
    dataset_name: the name of the dataset, e.g. `mnist`.
    split: the split to which this file belongs, e.g. `train` or `test`.
    filetype_suffix: the suffix representing the filetype, e.g. `tfrecord`.
    shard_index: what shard this file is.
    num_shards: if known, the total number of shards.
    filename_template: the template to which this file conforms.
  """
  dataset_name: Optional[str] = None
  split: Optional[str] = None
  filetype_suffix: Optional[str] = None
  shard_index: Optional[int] = None
  num_shards: Optional[int] = None
  filename_template: Optional[ShardedFileTemplate] = None

  def full_filename_template(self):
    template = (
        self.filename_template or ShardedFileTemplate(data_dir=epath.Path('')))
    return template.replace(
        dataset_name=self.dataset_name,
        split=self.split,
        filetype_suffix=self.filetype_suffix,
    )

  @classmethod
  def from_str(
      cls,
      filename: str,
      filename_template: Optional[ShardedFileTemplate] = None,
  ) -> 'FilenameInfo':
    """Factory to create a `FilenameInfo` from filename."""
    filename_template = _get_filename_template(filename, filename_template)
    # Strip off the directory if the filename contains it.
    filename = os.path.basename(filename)
    filename_info = filename_template.parse_filename_info(filename)
    if filename_info is None:
      raise ValueError(f'Could not parse filename {filename} '
                       f'with template {filename_template}')
    return filename_info

  @staticmethod
  def is_valid(
      filename: str,
      filename_template: Optional[ShardedFileTemplate] = None,
  ) -> bool:
    """Returns True if the filename follow the given pattern."""
    filename_template = _get_filename_template(filename, filename_template)
    return filename_template.is_valid(filename)

  def __str__(self) -> str:
    return self.full_filename_template().relative_filepath(
        shard_index=self.shard_index, num_shards=self.num_shards)
