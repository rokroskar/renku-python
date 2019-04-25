# -*- coding: utf-8 -*-
#
# Copyright 2017-2019 - Swiss Data Science Center (SDSC)
# A partnership between École Polytechnique Fédérale de Lausanne (EPFL) and
# Eidgenössische Technische Hochschule Zürich (ETHZ).
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
"""Model objects representing datasets."""

import configparser
import datetime
import os
import re
import uuid
from functools import partial

import attr
from attr.validators import instance_of
from dateutil.parser import parse as parse_date

from renku import errors
from renku._compat import Path

from . import _jsonld as jsonld

NoneType = type(None)

_path_attr = partial(
    jsonld.ib,
    converter=Path,
)


@jsonld.s(
    type='schema:person',
    context={
        'dcterms': 'http://purl.org/dc/terms/',
        'schema': 'http://schema.org/'
    },
    frozen=True,
    slots=True,
)
class Author(object):
    """Represent the author of a resource."""

    identifier = jsonld.ib(context='@id')
    name = jsonld.ib(validator=instance_of(str), context='dcterms:name')
    email = jsonld.ib(context='dcterms:email')
    affiliation = jsonld.ib(default=None, context='schema:affiliation')

    @email.validator
    def check_email(self, attribute, value):
        """Check that the email is valid."""
        if not (
            isinstance(value, str) and re.match(r"[^@]+@[^@]+\.[^@]+", value)
        ):
            raise ValueError('Email address is invalid.')

    @classmethod
    def from_git(cls, git):
        """Create an instance from a Git repo."""
        git_config = git.config_reader()
        try:
            name = git_config.get_value('user', 'name', None)
            email = git_config.get_value('user', 'email', None)
        except (
            configparser.NoOptionError, configparser.NoSectionError
        ):  # pragma: no cover
            raise errors.ConfigurationError(
                'The user name and email are not configured. '
                'Please use the "git config" command to configure them.\n\n'
                '\tgit config --global --add user.name "John Doe"\n'
                '\tgit config --global --add user.email '
                '"john.doe@example.com"\n'
            )

        # Check the git configuration.
        if name is None:  # pragma: no cover
            raise errors.MissingUsername()
        if email is None:  # pragma: no cover
            raise errors.MissingEmail()

        return cls(name=name, email=email, identifier=email)

    @classmethod
    def from_commit(cls, commit):
        """Create an instance from a Git commit."""
        return cls(
            name=commit.author.name,
            email=commit.author.email,
        )


@attr.s
class AuthorsMixin:
    """Mixin for handling authors container."""

    authors = jsonld.container.list(
        Author, kw_only=True, context={'@id': 'dcterms:creator'}
    )

    @property
    def authors_csv(self):
        """Comma-separated list of authors associated with dataset."""
        return ','.join(author.name for author in self.authors)


@jsonld.s(
    type='schema:DigitalDocument',
    slots=True,
    context={
        'dcterms': 'http://purl.org/dc/terms/',
        'schema': 'http://schema.org/'
    }
)
class DatasetFile(AuthorsMixin):
    """Represent a file in a dataset."""

    path = _path_attr(kw_only=True)
    url = jsonld.ib(default=None, context='schema:url', kw_only=True)
    authors = jsonld.container.list(
        Author, kw_only=True, context={'@id': 'dcterms:creator'}
    )
    dataset = attr.ib(default=None, kw_only=True)
    added = jsonld.ib(context='schema:dateCreated', kw_only=True)

    @added.default
    def _now(self):
        """Define default value for datetime fields."""
        return datetime.datetime.utcnow()

    @property
    def full_path(self):
        """Return full path in the current reference frame."""
        return Path(
            os.path.realpath(str(self.__reference__.parent / self.path))
        )


def _parse_date(value):
    """Convert date to datetime."""
    if isinstance(value, datetime.datetime):
        return value
    return parse_date(value)


def _convert_dataset_files(value):
    """Convert dataset files."""
    output = {}
    for k, v in value.items():
        inst = DatasetFile.from_jsonld(v)
        output[inst.path] = inst
    return output


@jsonld.s(
    type='dctypes:Dataset',
    context={
        'dcterms': 'http://purl.org/dc/terms/',
        'dctypes': 'http://purl.org/dc/dcmitypes/',
        'schema': 'http://schema.org/'
    },
)
class Dataset(AuthorsMixin):
    """Repesent a dataset."""

    SUPPORTED_SCHEMES = ('', 'file', 'http', 'https', 'git+https', 'git+ssh')

    name = jsonld.ib(type=str, context='dcterms:name')

    created = jsonld.ib(
        converter=_parse_date,
        context='schema:dateCreated',
    )

    identifier = jsonld.ib(
        default=attr.Factory(uuid.uuid4),
        converter=lambda x: uuid.UUID(str(x)),
        context='@id'
    )

    authors = jsonld.container.list(Author, context={'@id': 'dcterms:creator'})

    files = jsonld.container.index(
        DatasetFile,
        converter=_convert_dataset_files,
        context={'@id': 'schema:DigitalDocument'}
    )

    @created.default
    def _now(self):
        """Define default value for datetime fields."""
        return datetime.datetime.utcnow()

    @property
    def short_id(self):
        """Shorter version of identifier."""
        return str(self.identifier)[:8]

    @property
    def authors_csv(self):
        """Comma-separated list of authors associated with dataset."""
        return ','.join(author.name for author in self.authors)

    def rename_files(self, rename):
        """Rename files using the path mapping function."""
        files = {}

        for key, file in self.files.items():
            key = rename(key)
            files[key] = attr.evolve(file, path=key)

        # TODO consider creating custom evolve function
        renamed = attr.evolve(self, files=files)
        setattr(renamed, '__reference__', self.__reference__)
        setattr(renamed, '__source__', self.__source__.copy())
        return renamed

    def unlink_file(self, file_path):
        """Unlink a file from dataset.

        :param file_path: Relative path used as key inside files container.
        """
        return self.files.pop(file_path, None)
