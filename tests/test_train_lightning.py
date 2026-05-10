"""Tests for the PyTorch Lightning LSTM training module."""
import pytest
from unittest.mock import patch, MagicMock
import torch
from torch.utils.data import TensorDataset, DataLoader


class TestAirglowLSTM:
    """Test the PyTorch Lightning LSTM model."""

    def test_model_initialization(self):
        """Model should initialize with correct hyperparameters."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(
            input_size=21, hidden_size=50, num_layers=2,
            learning_rate=0.001, late_penalty=5.0
        )
        assert model.hparams.input_size == 21
        assert model.hparams.hidden_size == 50
        assert model.hparams.num_layers == 2
        assert model.hparams.learning_rate == 0.001
        assert model.late_penalty == 5.0

    def test_model_forward_pass(self):
        """Model should produce correct output shape."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(4, 50, 21)
        output = model(x)
        assert output.shape == (4, 1)

    def test_model_forward_pass_single(self):
        """Model should handle single sample."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(1, 50, 21)
        output = model(x)
        assert output.shape == (1, 1)

    def test_asymmetric_loss_perfect(self):
        """Asymmetric loss should be zero for perfect predictions."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        pred = torch.tensor([10.0, 20.0, 30.0])
        target = torch.tensor([10.0, 20.0, 30.0])
        loss = model.asymmetric_loss(pred, target)
        assert loss.item() == pytest.approx(0.0, abs=1e-6)

    def test_asymmetric_loss_underestimation(self):
        """Underestimation should be penalized more."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2, late_penalty=5.0)

        # Underestimation: pred=10, target=20
        pred_under = torch.tensor([10.0])
        target = torch.tensor([20.0])
        loss_under = model.asymmetric_loss(pred_under, target)

        # Overestimation: pred=30, target=20
        pred_over = torch.tensor([30.0])
        loss_over = model.asymmetric_loss(pred_over, target)

        assert loss_under > loss_over

    def test_configure_optimizers(self):
        """Model should return optimizer and scheduler."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        result = model.configure_optimizers()

        assert "optimizer" in result
        assert "lr_scheduler" in result
        assert result["lr_scheduler"]["monitor"] == "val_loss"

    def test_training_step(self):
        """Training step should return loss."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(4, 50, 21)
        y = torch.randn(4, 1)
        batch = (x, y)

        loss = model.training_step(batch, 0)
        assert loss is not None
        assert loss.item() > 0

    def test_validation_step(self):
        """Validation step should return loss."""
        from src.scripts.train_lightning import AirglowLSTM

        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(4, 50, 21)
        y = torch.randn(4, 1)
        batch = (x, y)

        loss = model.validation_step(batch, 0)
        assert loss is not None


class TestTrainLightning:
    """Test the train_lightning function."""

    def test_function_signature(self):
        """Train function should accept expected parameters."""
        import inspect
        from src.scripts.train_lightning import train_lightning

        sig = inspect.signature(train_lightning)
        params = list(sig.parameters.keys())
        assert "data_path" in params
        assert "epochs" in params
        assert "batch_size" in params
        assert "learning_rate" in params
        assert "hidden_size" in params
        assert "num_layers" in params
        assert "patience" in params
        assert "val_split" in params
        assert "mlflow_tracking_uri" in params

    def test_function_defaults(self):
        """Train function should have sensible defaults."""
        import inspect
        from src.scripts.train_lightning import train_lightning

        sig = inspect.signature(train_lightning)
        assert sig.parameters["epochs"].default == 50
        assert sig.parameters["batch_size"].default == 32
        assert sig.parameters["learning_rate"].default == 0.001
        assert sig.parameters["patience"].default == 10
        assert sig.parameters["val_split"].default == 0.2
