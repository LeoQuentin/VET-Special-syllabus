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


# because pytorch is dumb we have to do __init__:
if __name__ == "__main__":
    efficientnet_versions = ['b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6', 'b7']

    # Size
    size = (384, 384)

    # Training parameters
    training_params = {
        "model_id": efficientnet_versions,
        "batch_size": 32,
        "early_stopping_patience": 5,
        "max_time_hours": 12,
        "train_folds": [0, 1, 2],
        "val_folds": [3],
        "test_folds": [4],
        "log_every_n_steps": 4,
        "presicion": "16-mixed",
        "size": size
    }

    class EfficientNet(BaseNormalAbnormal):
        def __init__(self, model_config, learning_rate=3e-4, *args, **kwargs):
            # Initialize the model with specific configuration
            model = AutoModelForImageClassification.from_config(model_config)
            model.classifier = torch.nn.Linear(model.classifier.in_features, 2)
            super().__init__(model=model, *args, **kwargs)

            # set learning rate
            self.learning_rate = learning_rate

        def configure_optimizers(self):
            optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
            lr_scheduler = {'scheduler': ReduceLROnPlateau(optimizer,
                                                           mode='min',
                                                           factor=0.2,
                                                           patience=7),
                            'monitor': 'val_loss',  # Specify the metric you want to monitor
                            'interval': 'epoch',
                            'frequency': 1}
            return [optimizer], [lr_scheduler]

    # --------------------- Model ---------------------

    for version in efficientnet_versions:
        model_id = f"google/efficientnet-{version}"
        config = AutoConfig.from_pretrained(model_id)
        config.image_size = size
        config.num_channels = 1  # Assuming grayscale images, adjust if different

        # ------------------ Instanciate model ------------------

        model = EfficientNet(model_config=config)

        # --------------------- Preprocessing ---------------------

        def train_preprocess(image):
            # image is a numpy array in the shape (H, W, C)
            image = np_image_to_PIL(image)  # convert to PIL image

            # Preprocess the image
            transform_pipeline = transforms.Compose([
                transforms.Resize(size),
                transforms.Grayscale(num_output_channels=1),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor()
            ])
            image = transform_pipeline(image)

            # Extract features using the feature extractor from Huggingface
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
        checkpoint = os.path.join(project_root, "src/experiments/modelcheckpoints")
        model_checkpoint = ModelCheckpoint(dirpath=checkpoint,
                                           filename=f'efficientnet_{version}_best_checkpoint' + '_{epoch:02d}_{val_loss:.2f}',  # noqa
                                           monitor='val_loss',
                                           mode='min',
                                           save_top_k=1)

        # Logger
        log_dir = checkpoint = os.path.join(project_root, "src/experiments/logs")
        logger = CSVLogger(save_dir=log_dir,
                           name=f"efficientnet_{version}",
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

        # Best model path
        best_model_path = model_checkpoint.best_model_path
        print(f"Best model path: {best_model_path}")
