import base64
import copy
import hashlib
import os
import pathlib as pl
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence, Tuple

import yaml

PIPELINE_NAMES = {
    "ABCD": "cpac_abcd-options",
    "CCS": "cpac_ccs-options",
    "RBC": "RBCv0",
    "fMRIPrep": "cpac_fmriprep-options",
}
"""Maps pipeline names to pipeline IDs for all pipelines that should be included in the generation."""

CONNECTIVITY_METHODS = ["AFNI", "Nilearn"]
"""Connectivity methods to include in the generation."""

NUISANCE_METHODS = [True, False]
"""Nuisance methods to include in the generation."""


MergePath = List[str]
"""A path in the C-PAC config (to merge from a perturbed pipeline)"""

@dataclass
class PipelineStep:
    """Represents a pipeline step that should be merged from a perturbed pipeline"""

    name: str
    merge_paths: List[MergePath]


PIPELINE_STEPS: List[PipelineStep] = [
    PipelineStep(name="Structural Masking", merge_paths=[["anatomical_preproc"]]),
    PipelineStep(
        name="Structural Registration",
        merge_paths=[
            ["registration_workflows", "anatomical_registration"],
        ],
    ),
    PipelineStep(
        name="Functional Masking", merge_paths=[["functional_preproc", "func_masking"]]
    ),
    PipelineStep(
        name="Functional Registration",
        merge_paths=[
            ["registration_workflows", "functional_registration", "coregistration"],
        ],
    ),
]
"""Pipeline steps and their paths in the C-PAC config to include in the generation."""


# Utilities


@contextmanager
def cd(path):
    """Context manager for changing the working directory"""
    old_wd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_wd)


def download_cpac_configs(checkout_sha: str, dir_configs: pl.Path):
    """Downloads C-PAC configs from github and extracts them to the specified directory"""

    print(f"Check out C-PAC ({checkout_sha}) from github...")
    print(f"-------------------------------------------")
    os.system("git clone https://github.com/FCP-INDI/C-PAC.git dist/temp_cpac")
    with cd("dist/temp_cpac"):
        if os.system(f'git checkout "{checkout_sha}"') != 0:
            print(f"Could not checkout {checkout_sha}")
            exit(1)
    print(f"-------------------------------------------")

    print(f"Extracting configs...")
    os.system(f"cp -r dist/temp_cpac/CPAC/resources/configs {dir_configs}")

    print(f"Removing C-PAC...")
    os.system("rm -rf dist/temp_cpac")


@dataclass
class PipelineConfig:
    """Represents a C-PAC pipeline configuration"""

    name: str
    file: pl.Path
    config: dict

    def clone(self) -> "PipelineConfig":
        return PipelineConfig(
            name=self.name, file=self.file, config=copy.deepcopy(self.config)
        )

    def set_name(self, name: str) -> None:
        self.name = name
        self.config["pipeline_setup"]["pipeline_name"] = name

    def dump(self, exist_ok=False) -> None:
        if self.file.exists() and not exist_ok:
            raise FileExistsError(f"File {self.file} already exists")
        with open(self.file, "w") as handle:
            yaml.dump(self.config, handle)


def multi_get(obj: dict, index: Iterable) -> Optional[Any]:
    """
    Gets a value from a nested dictionary.
    Returns None if the path does not exist.
    """
    for i in index:
        if not isinstance(obj, dict) or i not in obj:
            return None
        obj = obj[i]
    return obj


def multi_set(obj: dict, index: Sequence, value: Any) -> bool:
    """
    Sets a value in a nested dictionary.
    Returns True if the path exists or was able to be created
    and the value was set.
    """
    for idx, i in enumerate(index):
        if not isinstance(obj, dict):
            return False

        if idx == len(index) - 1:
            obj[i] = value
            return True

        if i not in obj:
            obj[i] = {}

        obj = obj[i]
    assert False


def multi_del(obj: dict, index: Sequence) -> Optional[Any]:
    """
    Deletes a value from a nested dictionary.
    Returns the value if the path exists and
    the value was deleted.
    """
    for idx, i in enumerate(index):
        if not isinstance(obj, dict):
            return None

        if idx == len(index) - 1:
            if i in obj:
                val = obj[i]
                del obj[i]
                return val
            return None

        if i not in obj:
            return None

        obj = obj[i]
    assert False


def filesafe(s: str, replacement: str = "-"):
    """
    Converts a string to a file safe string.
    Removes all non-alphanumeric characters and
    replaces them with the replacement string.
    """
    return re.sub(r"[^\w\d-]", replacement, s).lower()


def aslist(obj: Any):
    """
    Converts an object to a list. If the object is
    already a list, it is returned as is.
    """
    if isinstance(obj, list):
        return obj
    return [obj]


def b64_urlsafe_hash(s: str):
    """
    Hashes a string and returns a base64 urlsafe encoded version of the hash.
    """
    return (
        base64.urlsafe_b64encode(hashlib.sha1(s.encode()).digest())
        .decode()
        .replace("=", "")
    )


# Generation


@dataclass
class PipelineCombination:
    """Represents a combination of all parameters pipeline generation should be run for"""

    pipeline_label: str
    pipeline_id: str
    pipeline_perturb_label: str
    pipeline_perturb_id: str
    step: PipelineStep
    connectivity_method: str
    use_nuisance_correction: bool

    def name(self, pipeline_num: int) -> str:
        return (
            f"p{pipeline_num:03d}_"
            f"base-{filesafe(self.pipeline_label)}_"
            f"perturb-{filesafe(self.pipeline_perturb_label)}_"
            f"step-{filesafe(self.step.name)}_"
            f"conn-{filesafe(self.connectivity_method)}_"
            f"nuisance-{filesafe(str(self.use_nuisance_correction))}"
        )

    def filename(self, pipeline_num: int) -> str:
        return self.name(pipeline_num) + ".yml"


def iter_pipeline_combis() -> Generator[PipelineCombination, Any, None]:
    """
    Iterate over all possible parameter combinations.

    From the heights of these pyramids, forty centuries look down on us.
    - Napoleon Bonaparte
    """
    for pipeline_label, pipeline_id in PIPELINE_NAMES.items():
        for pipeline_perturb_label, pipeline_perturb_id in PIPELINE_NAMES.items():
            for step in PIPELINE_STEPS:
                for connectivity_method in CONNECTIVITY_METHODS:
                    for nuisance_method in NUISANCE_METHODS:
                        yield PipelineCombination(
                            pipeline_id=pipeline_id,
                            pipeline_label=pipeline_label,
                            pipeline_perturb_id=pipeline_perturb_id,
                            pipeline_perturb_label=pipeline_perturb_label,
                            step=step,
                            connectivity_method=connectivity_method,
                            use_nuisance_correction=nuisance_method,
                        )


def iter_pipeline_combis_no_duplicates() -> Generator[PipelineCombination, Any, None]:
    """Iterates over all pipeline combinations that are not duplicates"""
    for combi in iter_pipeline_combis():
        if combi.pipeline_id != combi.pipeline_perturb_id:
            yield combi


def load_pipeline_config(pipeline_config_file: pl.Path) -> PipelineConfig:
    """Loads a pipeline config from a file and returns the pipeline name and config"""
    with open(pipeline_config_file, "r") as handle:
        pipeline_config = yaml.safe_load(handle)
    return PipelineConfig(
        name=pipeline_config["pipeline_setup"]["pipeline_name"],
        file=pipeline_config_file,
        config=pipeline_config,
    )


ConfigLookupTable = Dict[str, PipelineConfig]
"""A dictionary of pipeline name to config"""


def cpac_dir_to_lookup(dir_configs: pl.Path) -> ConfigLookupTable:
    """Loads all pipeline configs from a directory and returns a dictionary of pipeline name to config"""
    configs: ConfigLookupTable = {}
    for pipeline_config_file in dir_configs.glob(f"pipeline_config_*.yml"):
        pipeline_config = load_pipeline_config(pipeline_config_file)

        # Detect duplicate pipeline names
        pipeline_unique_name = pipeline_config.name
        while pipeline_unique_name in configs:
            print(
                f"WARNING: Duplicate pipeline name: "
                f"{pipeline_unique_name}: "
                f"{pipeline_config_file} - {configs[pipeline_unique_name].file}"
            )
            pipeline_unique_name += "_dup"

        configs[pipeline_unique_name] = pipeline_config
    return configs


def generate_pipeline_from_combi(
    pipeline_num: int, combi: PipelineCombination, configs: ConfigLookupTable
) -> PipelineConfig:
    # Copy pipeline
    pipeline = configs[combi.pipeline_id].clone()
    pipeline_perturb = configs[combi.pipeline_perturb_id].clone()

    # Merge perturbation step
    for merge_path in combi.step.merge_paths:
        snippet = multi_get(pipeline_perturb.config, index=merge_path)

        if snippet is None:
            print(f"WARNING: Cant find path {merge_path} in {pipeline_perturb.name}")
            multi_del(pipeline.config, index=merge_path)
            continue

        multi_set(pipeline.config, index=merge_path, value=snippet)

    # Set connectivity method
    multi_set(
        pipeline.config,
        index=["timeseries_extraction", "run"],
        value=True,
    )
    multi_set(
        pipeline.config,
        index=["timeseries_extraction", "connectivity_matrix", "using"],
        value=aslist(combi.connectivity_method),
    )
    multi_set(
        pipeline.config,
        index=["timeseries_extraction", "connectivity_matrix", "measure"],
        value=aslist("Pearson"),
    )

    # Set nuisance method
    multi_set(
        pipeline.config,
        index=["nuisance_corrections", "2-nuisance_regression", "run"],
        value=aslist(combi.use_nuisance_correction),
    )

    # Set pipeline name
    pipeline.set_name(combi.name(pipeline_num))

    return pipeline


def main(checkout_sha="89160708710aa6765479949edaca1fe18e4f65e3"):
    """Main entry point for the CLI"""

    cpac_version_hash = b64_urlsafe_hash(checkout_sha)

    dir_dist = pl.Path("dist")
    dir_build = pl.Path("build")
    dir_build.mkdir(parents=True, exist_ok=True)
    dir_configs = dir_build / f"cpac_source_configs_{cpac_version_hash}"

    # Download C-PAC configs
    if not dir_configs.exists():
        download_cpac_configs(checkout_sha, dir_configs)

    # Load pipeline YAMLS
    configs = cpac_dir_to_lookup(dir_configs)

    # Check that all pipelines are present
    for pipeline_label, pipeline_id in PIPELINE_NAMES.items():
        if not pipeline_id in configs:
            print(f"ERROR: Could not find pipeline {pipeline_label}")
            exit(1)

    # Generate pipelines
    dir_gen = dir_build / "gen192_nofork"
    dir_gen.mkdir(parents=True, exist_ok=True)

    print(f'Generating in folder "{dir_gen}"')

    for pipeline_num, combi in enumerate(iter_pipeline_combis_no_duplicates()):
        filename = combi.filename(pipeline_num)

        print(f"Generating {filename}")

        combined = generate_pipeline_from_combi(pipeline_num, combi, configs)
        combined.file = dir_gen / filename

        # Write pipeline
        combined.dump(exist_ok=False)

    # Zip all folders in build
    for subfolder in dir_build.glob("*"):
        if subfolder.is_dir():
            shutil.make_archive(
                base_name=str(dir_dist / subfolder.name),
                format="zip",
                root_dir=subfolder,
            )


if __name__ == "__main__":
    main()
