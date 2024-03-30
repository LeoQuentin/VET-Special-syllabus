import torch # noqa
from torchvision import transforms
import os
from datetime import timedelta
import sys
import dotenv

# huggingface model
from transformers import AutoConfig, AutoModelForImageClassification
# Lightning
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import EarlyStopping, ModelCheckpoint, LearningRateMonitor
from pytorch_lightning.loggers import CSVLogger
from torch.optim.lr_scheduler import ReduceLROnPlateau

dotenv.load_dotenv()
project_root = os.getenv('PROJECT_ROOT')
if project_root:
    sys.path.append(project_root)
from src.models.BaseNormalAbnormal import BaseNormalAbnormal # noqa
from src.utilities.H5DataModule import H5DataModule # noqa
from src.utilities.np_image_to_PIL import np_image_to_PIL # noqa
from src.augmentation.autoaugment import ImageNetPolicy # noqa

log_dir = os.path.join(project_root, "src/experiments/initial_model_comparison/binary/lightAugReg/logs") # noqa
checkpoint = os.path.join(project_root, "src/experiments/initial_model_comparison/binary/lightAugReg/modelcheckpoints") # noqa

# --------------- Everything that should be changed between experiments ---------------

size = (384, 384)
log_checkpoint_suffix = f"binary_lightAugReg_{size[0]}"


def train_preprocess(image):
    # image is a numpy array in the shape (H, W, C)
    image = np_image_to_PIL(image)  # convert to PIL image

    # Preprocess the image
    transform_pipeline = transforms.Compose([
        transforms.Resize(size),
        transforms.RandomRotation(10),
        transforms.Grayscale(num_output_channels=1),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor()
    ])
    image = transform_pipeline(image)

    # Remove the batch dimension if it exists
    if len(image.size()) == 4:
        image = image.squeeze(0)
    return image


def val_test_preprocess(image):
    # basically same as train_preprocess but without the augmentations
    image = np_image_to_PIL(image)  # convert to PIL image

    transform_pipeline = transforms.Compose([
        transforms.Resize(size),
        transforms.Grayscale(num_output_channels=1),
        transforms.ToTensor()
    ])
    image = transform_pipeline(image)

    if len(image.size()) == 4:
        image = image.squeeze(0)
    return image


# --------------------- Model ---------------------
# because pytorch is dumb we have to do __init__:
if __name__ == "__main__":
    # Model ID
    for models in ["vit-base-patch16-384"]:
        model_id = f"google/{models}"
        config = AutoConfig.from_pretrained(model_id)

        # Size
        config.image_size = size

        # Training parameters
        training_params = {
            "model_id": model_id,
            "batch_size": (32 if models in ["efficientnet-b0", "efficientnet-b1",
                                            "efficientnet-b2", "efficientnet-b3"] else 10),
            "early_stopping_patience": 25,
            "max_time_hours": 12,
            "train_folds": [0, 1, 2],
            "val_folds": [3],
            "test_folds": [4],
            "log_every_n_steps": 10,
            "presicion": "16-mixed",
            "size": config.image_size,
            "lr_scheduler_factor": 0.2,
            "lr_scheduler_patience": 8
        }

        # Channels
        config.num_channels = 1
        training_params["num_channels"] = config.num_channels

        # --------------------- Model ---------------------

        class NeuralNetwork(BaseNormalAbnormal):
            def __init__(self, *args, **kwargs):
                # Initialize the ConvNextV2 model with specific configuration
                model = AutoModelForImageClassification.from_config(config)
                model.classifier = torch.nn.Linear(model.classifier.in_features, 2)
                super().__init__(model=model, *args, **kwargs)

                # set learning rate
                self.learning_rate = 3e-4

            def configure_optimizers(self):
                optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
                lr_scheduler = {'scheduler': ReduceLROnPlateau(optimizer,
                                                               mode='min',
                                                               factor=training_params["lr_scheduler_factor"],  # noqa
                                                               patience=training_params["lr_scheduler_patience"]),  # noqa
                                'monitor': 'val_loss',  # Specify the metric you want to monitor
                                'interval': 'epoch',
                                'frequency': 1}
                return [optimizer], [lr_scheduler]

        # ------------------ Instanciate model ------------------

        model = NeuralNetwork()

        # --------------------- DataModule ---------------------

        dm = H5DataModule(os.getenv("DATA_FILE"),
                          batch_size=training_params["batch_size"],
                          train_folds=training_params["train_folds"],
                          val_folds=training_params["val_folds"],
                          test_folds=training_params["test_folds"],
                          target_var='target',
                          train_transform=train_preprocess,
                          val_transform=val_test_preprocess,
                          test_transform=val_test_preprocess
                          )

        # --------------------- Train ---------------------

        # callbacks
        early_stopping = EarlyStopping(monitor='val_loss',
                                       patience=training_params["early_stopping_patience"])
        model_checkpoint = ModelCheckpoint(dirpath=checkpoint,
                                           filename=f'{model_id}_{log_checkpoint_suffix}_best_checkpoint' + '_{epoch:02d}_{val_loss:.2f}',  # noqa
                                           monitor='val_loss',
                                           mode='min',
                                           save_top_k=1)

        # Logger
        logger = CSVLogger(save_dir=log_dir,
                           name=f"{model_id}_{log_checkpoint_suffix}",
                           flush_logs_every_n_steps=training_params["log_every_n_steps"])

        # Trainer
        trainer = Trainer(max_time=timedelta(hours=training_params["max_time_hours"]),
                          accelerator="auto",
                          callbacks=[early_stopping,
                                     model_checkpoint,
                                     LearningRateMonitor(logging_interval='step')],
                          logger=logger,
                          log_every_n_steps=training_params["log_every_n_steps"],
                          precision=training_params["presicion"])

        # Training
        trainer.fit(model, dm)
        trainer.test(model, dm)

        # Best model path
        best_model_path = model_checkpoint.best_model_path
        print(f"Best model path: {best_model_path}")
