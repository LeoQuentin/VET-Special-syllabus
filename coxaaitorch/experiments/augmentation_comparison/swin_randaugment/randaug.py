import torch  # noqa
import os
from datetime import timedelta
import dotenv
from coxaaitorch.augmentation.transforms import (
    no_augmentation,
    random_augmentation,
)
from functools import partial
from itertools import product

# for making the augmentation functions compatible with H5DataModule, hyperthreading/pickling issue
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import (
    EarlyStopping,
    ModelCheckpoint,
    LearningRateMonitor,
)
from pytorch_lightning.loggers import CSVLogger
from torch.optim.lr_scheduler import ReduceLROnPlateau

from coxaaitorch.utilities import H5DataModule, print_experiment_metrics
from coxaaitorch.models import BaseNetwork, create_model

dotenv.load_dotenv()

project_root = os.getenv("PROJECT_ROOT")

log_dir = os.path.join(
    project_root,
    "coxaaitorch/experiments/augmentation_comparison/swin_randaugment/logs",
)

checkpoint_dir = os.path.join(
    project_root,
    "coxaaitorch/experiments/augmentation_comparison/swin_randaugment/checkpoints",
)


training_params = {
    "batch_size": 16,
    "early_stopping_patience": 12,
    "max_time_hours": 6,
    "train_folds": [0, 1, 2],
    "val_folds": [3],
    "test_folds": [4],
    "log_every_n_steps": 10,
    "presicion": "16-mixed",
    "lr_scheduler_factor": 0.2,
    "lr_scheduler_patience": 7,
    "learning_rate": 5e-6,
}


class NeuralNetwork(BaseNetwork):
    def __init__(self, model_name, num_classes, size, *args, **kwargs):
        self.model_dict = create_model(
            model_name, size=size, pretrained=True, classes=num_classes, channels=3
        )
        model = self.model_dict["model"]
        super().__init__(model, num_classes=num_classes, *args, **kwargs)
        self.learning_rate = 5e-6

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(
            self.parameters(), lr=training_params["learning_rate"]
        )
        lr_scheduler = {
            "scheduler": ReduceLROnPlateau(
                optimizer,
                mode="min",
                factor=training_params["lr_scheduler_factor"],
                patience=training_params["lr_scheduler_patience"],
            ),
            "monitor": "val_loss",
            "interval": "epoch",
            "frequency": 1,
        }
        return [optimizer], [lr_scheduler]


if __name__ == "__main__":
    logger_directories = []
    for num_ops, magnitude in product(range(1, 4), range(1, 8)):

        model_name = "swin_base_patch4_window12_384_in22k"
        num_classes = 2
        size = (384, 384)

        # Create the model
        model = NeuralNetwork(model_name=model_name, num_classes=num_classes, size=size)

        preprocessor = model.model_dict["processor"]

        # Define the data module
        data_module = H5DataModule.from_base_config(
            {
                "train_transform": partial(
                    random_augmentation,
                    size=size[0],
                    channels=3,
                    num_ops=num_ops,
                    magnitute=magnitude,
                    preprocessor=preprocessor,
                ),
                "val_transform": partial(
                    no_augmentation, size=size[0], channels=3, preprocessor=preprocessor
                ),
                "test_transform": partial(
                    no_augmentation, size=size[0], channels=3, preprocessor=preprocessor
                ),
            }
        )

        # Define the logger
        logger = CSVLogger(log_dir, name=f"swin_{size[0]}_binary_randaugment_{num_ops}_{magnitude}")

        # Define the callbacks
        early_stopping = EarlyStopping(
            monitor="val_loss", patience=training_params["early_stopping_patience"]
        )
        model_checkpoint = ModelCheckpoint(
            dirpath=checkpoint_dir,
            filename=f"swin_{size[0]}_binary_randaugment_{num_ops}_{magnitude}"
            + "-{epoch:02d}-{val_loss:.2f}",
            monitor="val_loss",
            save_top_k=1,
            mode="min",
        )
        lr_monitor = LearningRateMonitor(logging_interval="epoch")

        # Define the trainer
        trainer = Trainer(
            logger=logger,
            callbacks=[early_stopping, model_checkpoint, lr_monitor],
            max_time=timedelta(hours=training_params["max_time_hours"]),
            log_every_n_steps=training_params["log_every_n_steps"],
            precision=training_params["presicion"],
        )
        # Fit the model
        trainer.fit(model, data_module)

        # Test the model
        trainer.test(model, datamodule=data_module)

        # Add logger directory to list
        logger_directories.append(logger.log_dir)

    # Print the best model metrics
    printout = print_experiment_metrics(logger_directories)
    with open(f"{log_dir}/best_model_metrics.txt", "w") as file:
        file.write(printout)