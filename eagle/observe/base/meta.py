# Copyright (c) 2009 IW.
# All rights reserved.
#
# Author: liuguiyang <liuguiyangnwpu@gmail.com>
# Date:   2018/2/26

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy
import warnings
import numpy as np
from abc import ABCMeta, abstractmethod

import eagle.utils as eu
from eagle.observe.base.basetype import BatchStatus
from eagle.observe.base.basetype import KeyPoint, KeyPointsOnImage
from eagle.observe.base.basetype import BoundingBox, BoundingBoxesOnImage
from eagle.observe.base.basebatch import HooksImages, HooksKeyPoints
from eagle.observe.base.basebatch import Batch, BatchLoader, BackgroundAugmentor


class Augmentor:
    __metaclass__ = ABCMeta
    
    def __init__(self, name=None, deterministic=False, random_state=None):
        """
        Create a new Augmenter instance.

        Parameters
        ----------
        name : None or string, optional(default=None)
            Name given to an Augmenter object. This name is used in print()
            statements as well as find and remove functions.
            If None, `UnnamedX` will be used as the name, where X is the
            Augmenter's class name.

        deterministic : bool, optional(default=False)
            Whether the augmenter instance's random state will be saved before
            augmenting images and then reset to that saved state after an
            augmentation (of multiple images/keypoints) is finished.
            I.e. if set to True, each batch of images will be augmented in the
            same way (e.g. first image might always be flipped horizontally,
            second image will never be flipped etc.).
            This is useful when you want to transform multiple batches of images
            in the same way, or when you want to augment images and keypoints
            on these images.
            Usually, there is no need to set this variable by hand. Instead,
            instantiate the augmenter with the defaults and then use
            `augmenter.to_deterministic()`.

        random_state : None or int or np.random.RandomState, optional(default=None)
            The random state to use for this
            augmenter.
                * If int, a new np.random.RandomState will be created using this
                  value as the seed.
                * If np.random.RandomState instance, the instance will be used directly.
                * If None, imgaug's default RandomState will be used, which's state can
                  be controlled using imgaug.seed(int).
            Usually there is no need to set this variable by hand. Instead,
            instantiate the augmenter with the defaults and then use
            `augmenter.to_deterministic()`.

        """
        super(Augmentor, self).__init__()
        self.name = name
        if name is None:
            self.name = "Unnamed" + self.__class__.__name__
        self.deterministic = deterministic
        if random_state is None:
            if self.deterministic:
                self.random_state = eu.new_random_state()
            else:
                self.random_state = eu.current_random_state()
        elif isinstance(random_state, np.random.RandomState):
            self.random_state = random_state
        else:
            self.random_state = np.random.RandomState(random_state)
        
        self.activated = True
        
    def augment_batches(self, batches):
        """
        Augment multiple batches of images.

        Parameters
        ----------
        batches : list
            List of image batches to augment.
            The expected input is a list, with each entry having one of the
            following datatypes:
                * ia.Batch
                * []
                * list of ia.KeypointsOnImage
                * list of (H,W,C) ndarray
                * list of (H,W) ndarray
                * (N,H,W,C) ndarray
                * (N,H,W) ndarray
            where N = number of images, H = height, W = width,
            C = number of channels.
            Each image is recommended to have dtype uint8 (range 0-255).

        Yields
        -------
        augmented_batch : ia.Batch or list of ia.KeypointsOnImage or list of (H,W,C) ndarray or list of (H,W) ndarray or (N,H,W,C) ndarray or (N,H,W) ndarray
            Augmented images/keypoints.
            Datatype usually matches the input datatypes per list element.
        """
        eu.do_assert(isinstance(batches, list))
        eu.do_assert(len(batches) > 0)

        enum_batch_status = BatchStatus()

        batches_normalized = []
        batches_original_dts = []
        for i, batch in enumerate(batches):
            if isinstance(batch, Batch):
                batch.data = (i, batch.data)
                batches_normalized.append(batch)
                batches_original_dts.append(enum_batch_status.IMG_AUG_BATCH)
            elif eu.is_np_array(batch):
                eu.do_assert(batch.ndim in (3, 4),
                             message="Expected numpy array to have shape(N,H,"
                                     "W) or (N,H,W,C)")
                batches_normalized.append(Batch(images=batch, data=i))
                batches_original_dts.append(enum_batch_status.NP_ARRAY)
            elif isinstance(batch, list):
                if len(batch) == 0:
                    batches_normalized.append(Batch())
                    batches_original_dts.append(enum_batch_status.EMPTY_LIST)
                elif eu.is_np_array(batch[0]):
                    batches_normalized.append(Batch(images=batch, data=i))
                    batches_original_dts.append(enum_batch_status.NP_ARRAYS)
                elif isinstance(batch[0], KeyPointsOnImage):
                    batches_normalized.append(Batch(keypoints=batch, data=i))
                    batches_original_dts.append(enum_batch_status.KPS_ON_IMAGE)
                else:
                    raise Exception("Unknown datatype in batch[0]. "
                                    "Expected numpy array or imgaug.KeypointsOnImage")
            else:
                raise Exception("Unknown datatype in of batch. Expected imgaug."
                                "Batch or numpy array or list of numpy arrays/imgaug.KeypointsOnImage.")

        def unnormalize_batch(batch_aug):
            i = batch_aug.data
            if isinstance(i, tuple):
                i = i[0]
            dt_orig = batches_original_dts[i]
            if dt_orig == enum_batch_status.IMG_AUG_BATCH:
                batch_unnormalized = batch_aug
                batch_unnormalized.data = batch_unnormalized.data[1]
            elif dt_orig == enum_batch_status.NP_ARRAY:
                batch_unnormalized = batch_aug.images_aug
            elif dt_orig == enum_batch_status.EMPTY_LIST:
                batch_unnormalized = []
            elif dt_orig == enum_batch_status.NP_ARRAYS:
                batch_unnormalized = batch_aug.images_aug
            elif dt_orig == enum_batch_status.KPS_ON_IMAGE:
                batch_unnormalized = batch_aug.keypoints_aug
            else:
                raise Exception("Internal Error. Unexpected value in dt_orig")
            return batch_unnormalized

        def load_batches():
            for batch in batches_normalized:
                yield batch

        batch_loader = BatchLoader(load_batches)
        bg_augmentor = BackgroundAugmentor(batch_loader, self)
        while True:
            batch_aug = bg_augmentor.get_batch()
            if batch_aug is None:
                break
            else:
                batch_unnormalized = unnormalize_batch(batch_aug)
                yield batch_unnormalized
        batch_loader.terminate()
        bg_augmentor.terminate()

    def augment_image(self, image):
        """Augment a single image.
        Parameters:
            image: (H,W,C) ndarray or (H,W) ndarray
            The image to augment. Should have dtype uint8
        Returns:
            image: ndarray
            The corresponding augmented image.
        """
        eu.do_assert(image.ndim in [2, 3],
                     message="Expected image to have shape(H,W,C).")
        return self.augment_images([image])[0]

    def augment_images(self, images, parents=None):
        """Augment multiple images.
        Parameters:
            images: list of image(H,W,C)
            parents: None or list of Augmentor, optional(default=None)
                Parent augmentors that have previously beem called before the
                call to this function. Usually you can leave this parameter
                as None. It is set automatically for child augmentors.
        Returns:
            images_results:ndarray or list, Corresponding augmentor images.
        """
        if self.deterministic:
            state_orig = self.random_state.get_state()
        if parents is None:
            parents = []
        if eu.is_np_array(images):
            input_type = "array"
            input_added_axis = False
            eu.do_assert(images.ndim in [3, 4],
                         message="Expected 3d/4d array of form (N, H, W) or ("
                                 "N, H, W, C)")
            images_copy = np.copy(images)
            if images_copy.ndim == 3 and images_copy.shape[-1] in [1, 3]:
                warnings.warn(
                    "You provided a numpy array of shape %s as input to augment_images(), "
                    "which was interpreted as (N, H, W). The last dimension however has "
                    "value 1 or 3, which indicates that you provided a single image "
                    "with shape (H, W, C) instead. If that is the case, you should use "
                    "augment_image(image) or augment_images([image]), otherwise "
                    "you will not get the expected augmentations." % (
                    images_copy.shape,))

            if images_copy.ndim == 3:
                images_copy == images_copy[..., np.newaxis]
                input_added_axis = True
        elif eu.is_iterable(images):
            input_type = "list"
            input_added_axis = []
            if len(images) == 0:
                images_copy = []
            else:
                eu.do_assert(all(image.ndim in [2, 3] for image in images),
                             message="Expected list of images with each image "
                                     "having shape(H, W) or (H, W, C)")
                images_copy = []
                input_added_axis = []
                for image in images:
                    image_copy = np.copy(image)
                    if image.ndim == 2:
                        image_copy = image_copy[:, :, np.newaxis]
                        input_added_axis.append(True)
                    else:
                        input_added_axis.append(False)
        else:
            raise Exception("Expected Images as one numpy array "
                            "or list/tuple of numpy arrays.")

        hooks = HooksImages()
        images_copy = hooks.preprocess(images_copy, augmentor=self, parents=parents)
        if hooks.is_activated(images_copy, augmentor=self, parents=parents,
                              default=self.activated):
            if len(images) > 0:
                images_result = self._augment_images()

    @abstractmethod
    def _augment_images(self, images, random_state, parents, hooks):
        """
        Augment multiple images.

        This is the internal variation of `augment_images()`.
        It is called from `augment_images()` and should usually not be called
        directly.
        It has to be implemented by every augmenter.
        This method may transform the images in-place.
        This method does not have to care about determinism or the
        Augmenter instance's `random_state` variable. The parameter
        `random_state` takes care of both of these.

        Parameters
        ----------
        images : (N,H,W,C) ndarray or list of (H,W,C) ndarray
            Images to augment.
            They may be changed in-place.
            Either a list of (H, W, C) arrays or a single (N, H, W, C) array,
            where N = number of images, H = height of images, W = width of
            images, C = number of channels of images.
            In the case of a list as input, H, W and C may change per image.

        random_state : np.random.RandomState
            The random state to use for all sampling tasks during the
            augmentation.

        parents : list of Augmenter
            See augment_images().

        hooks : ia.HooksImages
            See augment_images().

        Returns
        ----------
        images : (N,H,W,C) ndarray or list of (H,W,C) ndarray
            The augmented images.

        """
        raise NotImplementedError()

    def augment_keypoints(self, keypoints_on_images, parents=None, hooks=None):
        """
        Augment image keypoints.

        This is the corresponding function to `augment_images()`, just for
        keypoints/landmarks (i.e. coordinates on the image).
        Usually you will want to call `augment_images()` with a list of images,
        e.g. `augment_images([A, B, C])` and then `augment_keypoints()` with the
        corresponding list of keypoints on these images, e.g.
        `augment_keypoints([Ak, Bk, Ck])`, where `Ak` are the keypoints on
        image `A`.

        Make sure to first convert the augmenter(s) to deterministic states
        before augmenting images and their corresponding keypoints,
        e.g. by
            >>> seq = iaa.Fliplr(0.5)
            >>> seq_det = seq.to_deterministic()
            >>> imgs_aug = seq_det.augment_images([A, B, C])
            >>> kps_aug = seq_det.augment_keypoints([Ak, Bk, Ck])
        Otherwise, different random values will be sampled for the image
        and keypoint augmentations, resulting in different augmentations (e.g.
        images might be rotated by `30deg` and keypoints by `-10deg`).
        Also make sure to call `to_deterministic()` again for each new batch,
        otherwise you would augment all batches in the same way.


        Parameters
        ----------
        keypoints_on_images : list of ia.KeypointsOnImage
            The keypoints/landmarks to augment.
            Expected is a list of ia.KeypointsOnImage objects,
            each containing the keypoints of a single image.

        parents : None or list of Augmenter, optional(default=None)
            Parent augmenters that have previously been called before the
            call to this function. Usually you can leave this parameter as None.
            It is set automatically for child augmenters.

        hooks : None or ia.HooksKeypoints, optional(default=None)
            HooksKeypoints object to dynamically interfere with the
            augmentation process.

        Returns
        -------
        keypoints_on_images_result : list of ia.KeypointsOnImage
            Augmented keypoints.
        """
        if self.deterministic:
            state_orig = self.random_state.get_state()
        if parents is None:
            parents = []
        if hooks is None:
            hooks = HooksKeyPoints()

        eu.do_assert(eu.is_iterable(keypoints_on_images))
        eu.do_assert(all([isinstance(kps_on_img, KeyPointsOnImage)
                          for kps_on_img in keypoints_on_images]))
        kps_on_imgs_copy = [kps_on_img.deepcopy()
                            for kps_on_img in keypoints_on_images]
        kps_on_imgs_copy = hooks.preprocess(
            kps_on_imgs_copy, augmentor=self, parents=parents)

        if hooks.is_activated(kps_on_imgs_copy, augmentor=self, parents=parents,
                default=self.activated):
            if len(kps_on_imgs_copy) > 0:
                kps_on_imgs_result = self._augment_images(
                    images=kps_on_imgs_copy,
                    random_state=eu.copy_random_state(self.random_state),
                    parents=parents,
                    hooks=hooks)
                eu.forward_random_state(self.random_state)
            else:
                kps_on_imgs_result = kps_on_imgs_copy
        else:
            kps_on_imgs_result = kps_on_imgs_copy

        kps_on_imgs_result = hooks.postprocess(
            kps_on_imgs_result,augmentor=self, parents=parents)

        if self.deterministic:
            self.random_state.set_state(state_orig)

        return kps_on_imgs_result

    @abstractmethod
    def _augment_keypoints(self, keypoints_on_images, random_state, parents,
                           hooks):
        raise NotImplementedError()

    def augment_bounding_boxes(self, bounding_boxes_on_images, hooks=None):
        """
        Augment image bounding boxes.

        This is the corresponding function to `augment_keypoints()`, just for
        bounding boxes.
        Usually you will want to call `augment_images()` with a list of images,
        e.g. `augment_images([A, B, C])` and then `augment_bounding_boxes()`
        with the corresponding list of bounding boxes on these images, e.g.
        `augment_bounding_boxes([Abb, Bbb, Cbb])`, where `Abb` are the
        bounding boxes on image `A`.

        Make sure to first convert the augmenter(s) to deterministic states
        before augmenting images and their corresponding bounding boxes,
        e.g. by
            >>> seq = iaa.Fliplr(0.5)
            >>> seq_det = seq.to_deterministic()
            >>> imgs_aug = seq_det.augment_images([A, B, C])
            >>> bbs_aug = seq_det.augment_keypoints([Abb, Bbb, Cbb])
        Otherwise, different random values will be sampled for the image
        and bounding box augmentations, resulting in different augmentations
        (e.g. images might be rotated by `30deg` and bounding boxes by
        `-10deg`). Also make sure to call `to_deterministic()` again for each
        new batch, otherwise you would augment all batches in the same way.

        Parameters
        ----------
        bounding_boxes_on_images : list of ia.BoundingBoxesOnImage
            The bounding boxes to augment.
            Expected is a list of ia.BoundingBoxesOnImage objects,
            each containing the bounding boxes of a single image.

        hooks : None or ia.HooksKeypoints, optional(default=None)
            HooksKeypoints object to dynamically interfere with the
            augmentation process.

        Returns
        -------
        result : list of ia.BoundingBoxesOnImage
            Augmented bounding boxes.
        """
        kps_ois = []
        for bbs_oi in bounding_boxes_on_images:
            kps = []
            for bb in bbs_oi.bounding_boxes:
                kps.extend(bb.to_keypoints())
            kps_ois.append(KeyPointsOnImage(kps, shape=bbs_oi.shape))

        kps_ois_aug = self.augment_keypoints(kps_ois, hooks=hooks)
        results = []
        for img_idx, kps_oi_aug in enumerate(kps_ois_aug):
            bbs_aug = []
            for i in range(len(kps_oi_aug.keypoints) // 4):
                bb_kps = kps_oi_aug.keypoints[i*4:(i+1)*4]
                x1 = min([kp.x for kp in bb_kps])
                x2 = max([kp.x for kp in bb_kps])
                y1 = min([kp.y for kp in bb_kps])
                y2 = max([kp.x for kp in bb_kps])
                bbs_aug.append(
                    bounding_boxes_on_images[img_idx].bounding_boxes[i].copy(
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2))
            results.append(
                BoundingBoxesOnImage(
                    bounding_boxes=bbs_aug,
                    shape=kps_oi_aug.shape))
        return results

    def to_deterministic(self, n=None):
        eu.do_assert(n is None or n >= 1)
        if n is None:
            return self.to_deterministic(1)[0]
        else:
            return [self._to_deterministic() for _ in range(n)]

    def _to_deterministic(self):
        aug = self.copy()
        aug.random_state = eu.new_random_state()
        aug.deterministic = True
        return aug

    def reseed(self, random_state=None, deterministic_too=False):
        """Reseed this augmentor and all of its children(if it has any).
        This function is useful, when augmentatons are run in the background.
        Parameters:
            random_state: None or it or np.random.RandState, optional A
            RandomState that is used to sample seeds per augmentor.
            If int, the parameter will be used as a seed for a new RandomState.
            If None, a new RandomState will automatically be creatd.
            deterministic_too: bool, optional
            Whether to also change the seed of an augmentor 'A', if 'A' is
            deterministic. This is the case both when this augmentor object
            is 'A' or one of its children is 'A'.
        """
        eu.do_assert(isinstance(deterministic_too, bool))
        if random_state is None:
            random_state = eu.current_random_state()
        elif isinstance(random_state, np.random.RandomState):
            pass
        else:
            random_state = eu.new_random_state(random_state)

        if not self.deterministic or deterministic_too:
            seed = random_state.randint(0, 10 ** 6, 1)[0]
            self.random_state = eu.new_random_state(seed)

        for lst in self.get_children_lists():
            for aug in lst:
                aug.reseed(random_state=random_state,
                           deterministic_too=deterministic_too)

    def copy(self):
        return copy.copy(self)

    def deepcopy(self):
        return copy.deepcopy(self)

    def get_children_lists(self):
        return []

    def get_all_children(self, flat=False):
        results = []
        for lst in self.get_children_lists():
            for aug in lst:
                results.append(aug)
                children = aug.get_all_children(flat=flat)
                if flat:
                    results.extend(children)
                else:
                    results.append(children)
        return results

    @abstractmethod
    def get_parameters(self):
        raise NotImplementedError()

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        params = self.get_parameters()
        params_str = ", ".join([param.__str__() for param in params])
        return "%s(name=%s, parameters=%s, deterministic=%s)" % (
            self.__class__.__name__, self.name, params_str, self.deterministic)