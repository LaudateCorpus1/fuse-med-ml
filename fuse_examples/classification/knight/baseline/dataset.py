
import os
import sys

from typing import Callable, Optional

from fuse.data.visualizer.visualizer_default_3d import Fuse3DVisualizerDefault
from fuse.data.augmentor.augmentor_default import FuseAugmentorDefault
from fuse.data.augmentor.augmentor_toolbox import aug_op_affine, aug_op_color, aug_op_gaussian
from fuse.data.dataset.dataset_default import FuseDatasetDefault
from fuse.data.sampler.sampler_balanced_batch import FuseSamplerBalancedBatch

from fuse.utils.utils_param_sampler import FuseUtilsParamSamplerUniform as Uniform
from fuse.utils.utils_param_sampler import FuseUtilsParamSamplerRandInt as RandInt
from fuse.utils.utils_param_sampler import FuseUtilsParamSamplerRandBool as RandBool
from fuse_examples.tutorials.multimodality_image_clinical.download import download_and_extract_isic
from torch.utils.data.dataloader import DataLoader
from .input_processor import KiTSBasicInputProcessor
from fuse_examples.tutorials.multimodality_image_clinical.ground_truth_processor import FuseSkinGroundTruthProcessor
from fuse.data.data_source.data_source_default import FuseDataSourceDefault

from fuse.data.augmentor.augmentor_toolbox import rotation_in_3d, squeeze_3d_to_2d, unsqueeze_2d_to_3d
from fuse.utils.utils_hierarchical_dict import FuseUtilsHierarchicalDict
import torch
from .clinical_processor import KiCClinicalProcessor

def prepare_clinical(sample_dict: dict) -> dict:
    age = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.age')
    if age!=None and age > 0 and age < 120:
        age = torch.tensor(age / 120.0).reshape(-1)
    else:
        age = torch.tensor(-1.0).reshape(-1)
    
    bmi = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.bmi')
    if bmi!=None and bmi > 10 and bmi < 100:
        bmi = torch.tensor(bmi / 50.0).reshape(-1)
    else:
        bmi = torch.tensor(-1.0).reshape(-1)

    radiographic_size = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.radiographic_size')
    if radiographic_size!=None and radiographic_size > 0 and radiographic_size < 50:
        radiographic_size = torch.tensor(radiographic_size / 15.0).reshape(-1)
    else:
        radiographic_size = torch.tensor(-1.0).reshape(-1)
    
    preop_egfr = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.preop_egfr')
    if preop_egfr!=None and preop_egfr > 0 and preop_egfr < 200:
        preop_egfr = torch.tensor(preop_egfr / 90.0).reshape(-1)
    else:
        preop_egfr = torch.tensor(-1.0).reshape(-1)
    # turn categorical features into one hot vectors
    gender = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.gender_num')
    gender_one_hot = torch.zeros(len(GENDER_INDEX))
    if gender in GENDER_INDEX.values():
        gender_one_hot[gender] = 1

    comorbidities = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.comorbidities')
    comorbidities_one_hot = torch.zeros(len(COMORBIDITIES_INDEX))
    if comorbidities in COMORBIDITIES_INDEX.values():
        comorbidities_one_hot[comorbidities] = 1
    
    smoking_history = FuseUtilsHierarchicalDict.get(sample_dict, 'data.input.clinical.smoking_history')
    smoking_history_one_hot = torch.zeros(len(SMOKE_HISTORY_INDEX))
    if smoking_history in SMOKE_HISTORY_INDEX.values():
        smoking_history_one_hot[smoking_history] = 1
    

    clinical_encoding = torch.cat((age, bmi, radiographic_size, preop_egfr, gender_one_hot, comorbidities_one_hot, smoking_history_one_hot), dim=0)
    FuseUtilsHierarchicalDict.set(sample_dict, "data.input.clinical.all", clinical_encoding)
    return sample_dict

def knight_dataset(data_dir: str = 'data', cache_dir: str = 'cache', split: dict = None, \
    reset_cache: bool = False, post_cache_processing_func: Optional[Callable] = None, \
        rand_gen = None, batch_size=8, resize_to=(256,256,110)):
    augmentation_pipeline = [
        [
            ("data.input.image",),
            rotation_in_3d,
            {
                "z_rot": Uniform(-5.0, 5.0),
                "y_rot": Uniform(-5.0, 5.0),
                "x_rot": Uniform(-5.0, 5.0),
            },
            {"apply": RandBool(0.5)},
        ],
        [("data.input.image",), squeeze_3d_to_2d, {"axis_squeeze": "z"}, {}],
        [
            ("data.input.image",),
            aug_op_affine,
            {
                "rotate": Uniform(0, 360.0),
                "translate": (RandInt(-14, 14), RandInt(-14, 14)),
                "flip": (RandBool(0.5), RandBool(0.5)),
                "scale": Uniform(0.9, 1.1),
            },
            {"apply": RandBool(0.9)},
        ],
        [
            ("data.input.image",),
            aug_op_gaussian,
            {"std": 0.01},
            {"apply": RandBool(0.9)},
        ],
        [
            ("data.input.image",),
            unsqueeze_2d_to_3d,
            {"channels": 1, "axis_squeeze": "z"},
            {},
        ],
    ]
    
    train_data_source = FuseDataSourceDefault(list(split['train']))

    # we use the same processor for the clinical data and ground truth, since both are in the .csv file
    # need to make sure to discard the label column from the data when using it as input
    input_processors = {
        'image': KiTSBasicInputProcessor(input_data=data_dir, resize_to=resize_to),
        'clinical': KiCClinicalProcessor(json_filename=os.path.join(data_dir, 'knight', 'data', 'knight.json'))
    }
 
    gt_processors = {
        'gt_global': KiCClinicalProcessor(json_filename=os.path.join(data_dir, 'knight', 'data', 'knight.json'), columns_to_tensor={'task_1_label':torch.long})
    }

    # Create data augmentation (optional)
    augmentor = FuseAugmentorDefault(
        augmentation_pipeline=augmentation_pipeline)

    # Create visualizer (optional)
    visualizer = Fuse3DVisualizerDefault(image_name = 'data.input.image', label_name='data.gt.gt_global.task_1_label')
    # Create dataset
    train_dataset = FuseDatasetDefault(cache_dest=cache_dir,
                                       data_source=train_data_source,
                                       input_processors=input_processors,
                                       gt_processors=gt_processors,
                                       post_processing_func=prepare_clinical,
                                       augmentor=augmentor,
                                       visualizer=visualizer)

    print(f'- Load and cache data:')
    train_dataset.create(reset_cache=reset_cache)
    
    print(f'- Load and cache data: Done')

    ## Create sampler
    print(f'- Create sampler:')
    sampler = FuseSamplerBalancedBatch(dataset=train_dataset,
                                       balanced_class_name='data.gt.gt_global.task_1_label',
                                       num_balanced_classes=2,
                                       batch_size=batch_size,
                                       use_dataset_cache=False) # we don't want to use_dataset_cache here since it's more 
                                                                # costly to read all cached data then simply the CSV file 
                                                                # which contains the labels

    print(f'- Create sampler: Done')

    ## Create dataloader
    train_dataloader = DataLoader(dataset=train_dataset,
                                  shuffle=False, drop_last=False,
                                  batch_sampler=sampler, collate_fn=train_dataset.collate_fn,
                                  num_workers=8, generator=rand_gen)
    print(f'Train Data: Done', {'attrs': 'bold'})

    #### Validation data
    print(f'Validation Data:', {'attrs': 'bold'})

    ## Create data source
    validation_data_source = FuseDataSourceDefault(list(split['val']))


    ## Create dataset
    validation_dataset = FuseDatasetDefault(cache_dest=cache_dir,
                                            data_source=validation_data_source,
                                            input_processors=input_processors,
                                            gt_processors=gt_processors,
                                            post_processing_func=prepare_clinical,
                                            visualizer=visualizer)

    print(f'- Load and cache data:')
    validation_dataset.create(pool_type='thread')  # use ThreadPool to create this dataset, to avoid cv2 problems in multithreading
    print(f'- Load and cache data: Done')

    ## Create dataloader
    validation_dataloader = DataLoader(dataset=validation_dataset,
                                       shuffle=False,
                                       drop_last=False,
                                       batch_sampler=None,
                                       batch_size=batch_size,
                                       num_workers=8,
                                       collate_fn=validation_dataset.collate_fn,
                                       generator=rand_gen)
    print(f'Validation Data: Done', {'attrs': 'bold'})

    return train_dataloader, validation_dataloader, train_dataset, validation_dataset


GENDER_INDEX = {
    'male': 0,
    'female': 1
}
COMORBIDITIES_INDEX = {
    'no comorbidities': 0,
    'comorbidities exist': 1
}
SMOKE_HISTORY_INDEX = {
    'never smoked': 0,
    'previous smoker': 1,
    'current smoker': 2
}
