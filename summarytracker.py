import datetime
import torch

import logging
import os
from shutil import copyfile

from typing import List, Union
from torch import nn
from torch import Tensor
import matplotlib

matplotlib.use("Agg")


import matplotlib.pyplot as plt
import numpy as np

from argparse import Namespace

from tensorboardX import SummaryWriter


logger = logging.getLogger(__name__)


class SummaryTracker:
    """
    The summary tracker stores all results and data that can be collected during an
    experiment.
    Main result locations are:
    - tensorboard: Tensorboard files
    - plots: Metric plots
    - checkpoints: Model checkpoints
    - args.txt: File containing all commandline arguments with which the
    experiment has been started
    """

    def __init__(self, metric_names: List[str], args: Namespace):
        """
        Initialize the Evaluator object.
        Args:
            metric_names: Names of different metrics
            args: Command line arguments
        """

        # Generate base path: ".../$(args.output_dir)/run-$(date)-$(tag)"
        _date_str = datetime.datetime.today().strftime("%y-%b-%d_%Hh:%Mm")
        tagstr = args.tag if args.tag == "" else "_" + args.tag

        self._base_dir = os.path.join(
            args.output_dir, "run_{0}{1}".format(_date_str, tagstr)
        )

        self._metric_names = metric_names
        self._metric_epochs_train = {name: [] for name in metric_names}
        self._metrics_epochs_val = {name: [] for name in metric_names}


        # File/Directory names
        self._args_path = os.path.join(self._base_dir, "args.txt")
        self._tensorboard_dir = os.path.join(self._base_dir, "tensorboard/")
        self._checkpoints_dir = os.path.join(self._base_dir, "checkpoints/")
        self._plots_dir = os.path.join(self._base_dir, "plots/")
        # Store best loss for model checkpoints
        self._best_val_loss = float("inf")
        self._best_cpt_path = os.path.join(self._checkpoints_dir, "best-model.pth")
        self._last_cpt_path = os.path.join(self._checkpoints_dir, "last-model.pth")
        self._create_dirs()

        # Tensorboard
        self._summary_writer = SummaryWriter(log_dir=self._tensorboard_dir)

        self._args = args

        # Store maxs
        self._max_epochs = args.epochs

        # Log template
        max_metric_name_len = max(map(len, metric_names))
        self._log_template = (
            "{progress: <10}{name: <10} ({metric_name: <"
            + str(max_metric_name_len)
            + "}): Train = {train_metric:10f}, Validation = {val_metric:10f}"
        )

    def _create_dirs(self):
        """Create necessary directories"""
        for d in [self._tensorboard_dir, self._checkpoints_dir, self._plots_dir]:
            self._ensure_dir(d)

    def _plot_loss(self):
        """Plot a 2x2 map of train/val loss values over the epochs"""
        if len(self._metric_names) != 4:
            logger.warning(
                "Number of metrics != 4 (was {}), skipping 2x2 "
                "plot.".format(len(self._metric_names))
            )
            return

        fig, axs = plt.subplots(nrows=2, ncols=2, constrained_layout=True)
        for loss_name, ax in zip(self._metric_names, axs.flatten()):
            train = np.array(self._metric_epochs_train[loss_name])
            val = np.array(self._metrics_epochs_val[loss_name])
            l1, = ax.plot(train[:, 0], train[:, 1], color="blue", label="train")
            l2, = ax.plot(val[:, 0], val[:, 1], color="green", label="val")
            ax.set_xlabel("epoch")
            ax.set_ylabel(loss_name)
            ax.legend([l1, l2], ["train", "val"], loc="upper right")
            ax.set_xlim((0, self._max_epochs))

        plt.savefig(os.path.join(self._plots_dir, "losses.png"))

    def _plot_metric(self, metric_dict: dict, xlabel: str, title: str, suffix: str):
        """
        Plot a specific metric
        Args:
            metric_dict (dict): Metric dictionary
            xlabel (str): X-Axis label
            title (str): Plot title
            suffix (str): File suffix
        """
        plt.figure()
        for name, metrics in metric_dict.items():
            data = np.array(metrics)
            plt.plot(data[:, 0], data[:, 1], label=name)
        plt.xlabel(xlabel)
        plt.ylabel("metric")
        plt.legend(loc="upper right")
        plt.title(title)
        plt.savefig(
            os.path.join(
                self._plots_dir, "{}-metric-{}.png".format(xlabel.lower(), suffix)
            )
        )

    def _plot_metric_epochs(self):
        """Plot metrics"""
        self._plot_metric(
            self._metric_epochs_train,
            xlabel="Epoch",
            title="Epochs: Train metric",
            suffix="train",
        )
        self._plot_metric(
            self._metrics_epochs_val,
            xlabel="Epoch",
            title="Epochs: Validation metric",
            suffix="val",
        )

    def _save_args(self):
        """Save arguments"""
        if self._args is None:
            return

        # Get maximum argument name length for formatting
        args = sorted(vars(self._args).items())
        length = max(map(lambda k: len(k[0]), args))

        # Save commandline args in a file
        line_template = "{0: <{2:}} = {1:}"
        with open(self._args_path, "w") as f:
            lines = [line_template.format(x, y, length) for x, y in args]
            header = "Command line arguments: \n"
            content = header + "\n".join(lines)
            f.write(content)

    def add_epoch_metric(
        self, epoch: int, train_metric: float, val_metric, metric_name: str,
            train: bool=False
    ) -> None:
        """
        Add a specific metric for a single epoch.
        Args:
            epoch (int): Epoch index
            train_metric (float): Train metric value
            val_metric (float): Validation metric value
            metric_name (str): metric name
            train (bool): Flag to indicate whether this is a train or validation value
        """
        # Store metric for plots
        self._metric_epochs_train[metric_name].append([epoch, train_metric])
        self._metrics_epochs_val[metric_name].append([epoch, val_metric])

        # Tensorboard
        self._summary_writer.add_scalars(
            main_tag=metric_name,
            tag_scalar_dict={"train": train_metric, "val": val_metric},
            global_step=epoch,
        )

        # Log
        logging.info(
            self._log_template.format(
                name="Epoch",
                metric_name=metric_name,
                train_metric=train_metric,
                val_metric=val_metric,
                progress="[{}/{}]".format(epoch, self._max_epochs),
            )
        )

    def add_image(self, epoch: int, img: Union[Tensor, np.ndarray], tag: str):
        """
        Add an image to the evaluation results
        Args:
            epoch (int): Current epoch
            img (Tensor or np.ndarray): Image
            tag (str): Tag as short description/identifier of the image
        """
        self._summary_writer.add_image(
            tag="image/" + tag, img_tensor=img, global_step=epoch
        )

    def add_disparity_map(self, epoch: int, disp: Tensor, tag: str):
        """
        Add an image to the evaluation results
        Args:
            epoch (int): Current epoch
            img (Tensor): Image
            tag (str): Tag as short description/identifier of the image
        """
        colorized_image = self._colorize_image(disp, vmin=0.0, vmax=1.0, cmap="plasma")
        self.add_image(epoch, colorized_image, tag)

    def add_checkpoint(self, model: nn.Module, val_loss: float) -> None:
        """
        Add a new checkpoint. Store latest model weights in checkpoints/last-model.pth
        and best model based on the current validation metric in
        checkpoints/best-model.pth.
        Args:
            model (nn.Module): PyTorch model
            val_loss (float): Latest validation loss
        """
        torch.save(model.state_dict(), f=self._last_cpt_path)
        if val_loss < self._best_val_loss:
            self._best_val_loss = val_loss
            torch.save(model.state_dict(), f=self._best_cpt_path)

    def save(self):
        """
        Save some results:
        - Log file
        - Arguments
        - Scalar values as JSON
        - Plots
        """
        # Copy log to the output-dir
        log_path = os.path.join(self._base_dir, "log.txt")
        copyfile(self._args.log_file, log_path)

        # Save arguments with which the current experiment has been started
        self._save_args()

        # Save all scalars to a json for future processing
        self._summary_writer.export_scalars_to_json(
            os.path.join(self._base_dir, "metric-results.json")
        )

        # Save plots
        self._plot_metric_epochs()
        self._plot_loss()

    def _ensure_dir(self, file: str) -> None:
        """
        Ensures that a given directory exists.

        Args:
            file: file
        """
        directory = os.path.dirname(file)
        if not os.path.exists(directory):
            os.makedirs(directory)

    def _colorize_image(self, value: Tensor, vmin=None, vmax=None, cmap=None) -> \
            np.ndarray:
        """
        A utility function for TensorFlow that maps a grayscale image to a matplotlib
        colormap for use with TensorBoard image summaries.

        By default it will normalize the input value to the range 0..1 before mapping
        to a grayscale colormap.

        Arguments:
          - value: 2D Tensor of shape [height, width] or 3D Tensor of shape
            [height, width, 1].
          - vmin: the minimum value of the range used for normalization.
            (Default: value minimum)
          - vmax: the maximum value of the range used for normalization.
            (Default: value maximum)
          - cmap: a valid cmap named for use with matplotlib's `get_cmap`.
            (Default: 'gray')


        Returns a 3D tensor of shape [height, width, 3].
        """

        # normalize
        vmin = value.min() if vmin is None else vmin
        vmax = value.max() if vmax is None else vmax
        value = (value - vmin) / (vmax - vmin)  # vmin..vmax

        # squeeze last dim if it exists
        value = value.squeeze()

        # quantize
        indices = (value * 255).long()

        # gather
        cm = matplotlib.cm.get_cmap(cmap if cmap is not None else "gray")
        value = np.array(cm.colors).take(indices=indices, axis=0)

        # Fix tensor shape to what the summary write expects (HCW)
        return value.transpose(2, 0, 1)