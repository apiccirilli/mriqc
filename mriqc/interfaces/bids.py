#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# @Author: oesteban
# @Date:   2016-06-03 09:35:13
from __future__ import print_function, division, absolute_import, unicode_literals
from os import getcwd
import os.path as op
import re
from io import open
import simplejson as json
from nipype import logging
from nipype.interfaces.base import (traits, isdefined, TraitedSpec,DynamicTraitedSpec,
                                    BaseInterfaceInputSpec, File, Undefined)
from mriqc.interfaces.base import MRIQCBaseInterface

IFLOGGER = logging.getLogger('interface')

class ReadSidecarJSONInputSpec(BaseInterfaceInputSpec):
    in_file = File(exists=True, mandatory=True, desc='the input nifti file')
    fields = traits.List(traits.Str, desc='get only certain fields')

class ReadSidecarJSONOutputSpec(TraitedSpec):
    subject_id = traits.Str()
    session_id = traits.Either(None, traits.Str())
    task_id = traits.Either(None, traits.Str())
    acq_id = traits.Either(None, traits.Str())
    rec_id = traits.Either(None, traits.Str())
    run_id = traits.Either(None, traits.Str())
    out_dict = traits.Dict()

class ReadSidecarJSON(MRIQCBaseInterface):
    """
    An utility to find and read JSON sidecar files of a BIDS tree
    """
    expr = re.compile('^(?P<subject_id>sub-[a-zA-Z0-9]+)(_(?P<session_id>ses-[a-zA-Z0-9]+))?'
                      '(_(?P<task_id>task-[a-zA-Z0-9]+))?(_(?P<acq_id>acq-[a-zA-Z0-9]+))?'
                      '(_(?P<rec_id>rec-[a-zA-Z0-9]+))?(_(?P<run_id>run-[a-zA-Z0-9]+))?')
    input_spec = ReadSidecarJSONInputSpec
    output_spec = ReadSidecarJSONOutputSpec

    def _run_interface(self, runtime):
        metadata = get_metadata_for_nifti(self.inputs.in_file)
        output_keys = [key for key in list(self.output_spec().get().keys()) if key.endswith('_id')]
        outputs = self.expr.search(op.basename(self.inputs.in_file)).groupdict()

        for key in output_keys:
            self._results[key] = outputs.get(key)

        if isdefined(self.inputs.fields) and self.inputs.fields:
            for fname in self.inputs.fields:
                self._results[fname] = metadata[fname]
        else:
            self._results['out_dict'] = metadata
        return runtime


class IQMFileSinkInputSpec(DynamicTraitedSpec, BaseInterfaceInputSpec):
    subject_id = traits.Str(mandatory=True, desc='the subject id')
    modality = traits.Str(mandatory=True, desc='the qc type')
    session_id = traits.Either(None, traits.Str, traits.Int, usedefault=True)
    task_id = traits.Either(None, traits.Str, usedefault=True)
    acq_id = traits.Either(None, traits.Str, usedefault=True)
    rec_id = traits.Either(None, traits.Str, usedefault=True)
    run_id = traits.Either(None, traits.Int, usedefault=True)

    root = traits.Dict(desc='output root dictionary')
    out_dir = File(desc='the output directory')
    _outputs = traits.Dict(value={}, usedefault=True)

    def __setattr__(self, key, value):
        if key not in self.copyable_trait_names():
            if not isdefined(value):
                super(IQMFileSinkInputSpec, self).__setattr__(key, value)
            self._outputs[key] = value
        else:
            if key in self._outputs:
                self._outputs[key] = value
            super(IQMFileSinkInputSpec, self).__setattr__(key, value)


class IQMFileSinkOutputSpec(TraitedSpec):
    out_file = File(desc='the output JSON file containing the IQMs')

class IQMFileSink(MRIQCBaseInterface):
    input_spec = IQMFileSinkInputSpec
    output_spec = IQMFileSinkOutputSpec
    BIDS_COMPONENTS = ['subject_id', 'session_id', 'task_id',
                       'acq_id', 'rec_id', 'run_id']
    expr = re.compile('^root[0-9]+$')

    def __init__(self, fields=None, force_run=True, **inputs):
        super(IQMFileSink, self).__init__(**inputs)

        if fields is None:
            fields = []

        self._out_dict = {}

        # Initialize fields
        fields = list(set(fields) - set(self.inputs.copyable_trait_names()))
        self._input_names = fields
        undefined_traits = {key: self._add_field(key) for key in fields}
        self.inputs.trait_set(trait_change_notify=False, **undefined_traits)

        if force_run:
            self._always_run = True

    def _add_field(self, name, value=Undefined):
        self.inputs.add_trait(name, traits.Any)
        self.inputs._outputs[name] = value
        return value

    def _process_name(self, name, val):
        if '.' in name:
            newkeys = name.split('.')
            name = newkeys.pop(0)
            nested_dict = {newkeys.pop(): val}

            for nk in reversed(newkeys):
                nested_dict = {nk: nested_dict}
            val = nested_dict

        return name, val

    def _gen_outfile(self):
        out_dir = getcwd()
        if isdefined(self.inputs.out_dir):
            out_dir = self.inputs.out_dir

        comp_ids = self.BIDS_COMPONENTS[1:]
        fname_comps = ['sub-%s' % self.inputs.subject_id]
        for comp in comp_ids:
            comp_val = getattr(self.inputs, comp, None)
            if isdefined(comp_val) and comp_val is not None:
                fname_comps.append('_%s-%s' % (comp[:3], comp_val))
        fname = (''.join(fname_comps).replace('_tas-', '_task-') +
                 '_%s.json' % self.inputs.modality)
        self._results['out_file'] = op.join(out_dir, fname)

        return self._results['out_file']

    def _run_interface(self, runtime):
        out_file = self._gen_outfile()

        if isdefined(self.inputs.root):
            self._out_dict = self.inputs.root

        root_adds = []
        for key, val in list(self.inputs._outputs.items()):
            if not isdefined(val) or key == 'trait_added':
                continue

            if not self.expr.match(key) is None:
                root_adds.append(key)
                continue

            key, val = self._process_name(key, val)
            self._out_dict[key] = val

        for root_key in root_adds:
            val = self.inputs._outputs.get(root_key, None)
            if isinstance(val, dict):
                self._out_dict.update(val)
            else:
                IFLOGGER.warn(
                    'Output "%s" is not a dictionary (value="%s"), '
                    'discarding output.', root_key, str(val))

        for comp in self.BIDS_COMPONENTS:
            comp_val = getattr(self.inputs, comp, None)
            if isdefined(comp_val) and comp_val is not None:
                self._out_dict[comp] = comp_val

        if self.inputs.modality == 'bold':
            self._out_dict['qc_type'] = 'func'
        elif self.inputs.modality == 'T1w':
            self._out_dict['qc_type'] = 'anat'

        with open(out_file, 'w') as f:
            f.write(json.dumps(self._out_dict, ensure_ascii=False))

        return runtime



def get_metadata_for_nifti(in_file):
    """Fetchs metadata for a given nifi file"""
    in_file = op.abspath(in_file)

    fname, ext = op.splitext(in_file)
    if ext == '.gz':
        fname, ext2 = op.splitext(fname)
        ext = ext2 + ext

    side_json = fname + '.json'
    fname_comps = op.basename(side_json).split("_")

    session_comp_list = []
    subject_comp_list = []
    top_comp_list = []
    ses = None
    sub = None

    for comp in fname_comps:
        if comp[:3] != "run":
            session_comp_list.append(comp)
            if comp[:3] == "ses":
                ses = comp
            else:
                subject_comp_list.append(comp)
                if comp[:3] == "sub":
                    sub = comp
                else:
                    top_comp_list.append(comp)

    if any([comp.startswith('ses') for comp in fname_comps]):
        bids_dir = '/'.join(op.dirname(in_file).split('/')[:-3])
    else:
        bids_dir = '/'.join(op.dirname(in_file).split('/')[:-2])

    top_json = op.join(bids_dir, "_".join(top_comp_list))
    potential_json = [top_json]

    subject_json = op.join(bids_dir, sub, "_".join(subject_comp_list))
    potential_json.append(subject_json)

    if ses:
        session_json = op.join(bids_dir, sub, ses, "_".join(session_comp_list))
        potential_json.append(session_json)

    potential_json.append(side_json)

    merged_param_dict = {}
    for json_file_path in potential_json:
        if op.isfile(json_file_path):
            with open(json_file_path, 'r') as jsonfile:
                param_dict = json.load(jsonfile)
                merged_param_dict.update(param_dict)

    return merged_param_dict
