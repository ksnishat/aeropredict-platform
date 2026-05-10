"""Tests for the AeroPredict LSTM training module."""
import pytest
import torch
import numpy as np
from src.train_model import AirglowLSTM, AsymmetricLoss


class TestAirglowLSTM:
    """Test the LSTM model architecture."""

    def test_model_initialization(self):
        """Model should initialize with correct default parameters."""
        model = AirglowLSTM()
        assert model.lstm.input_size == 21
        assert model.lstm.hidden_size == 50
        assert model.lstm.num_layers == 2
        assert model.fc.in_features == 50
        assert model.fc.out_features == 1

    def test_model_forward_pass(self):
        """Model should produce correct output shape."""
        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        # Batch of 4 sequences, each 50 timesteps, 21 features
        x = torch.randn(4, 50, 21)
        output = model(x)
        assert output.shape == (4, 1)

    def test_model_forward_pass_single(self):
        """Model should handle single sample."""
        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(1, 50, 21)
        output = model(x)
        assert output.shape == (1, 1)

    def test_model_with_different_input_sizes(self):
        """Model should work with different input sizes."""
        model = AirglowLSTM(input_size=10, hidden_size=32, num_layers=1)
        x = torch.randn(2, 30, 10)
        output = model(x)
        assert output.shape == (2, 1)

    def test_model_gradient_flow(self):
        """Gradients should flow through the model."""
        model = AirglowLSTM(input_size=21, hidden_size=50, num_layers=2)
        x = torch.randn(2, 50, 21)
        y = torch.randn(2, 1)

        output = model(x)
        loss = torch.nn.MSELoss()(output, y)
        loss.backward()

        # Check that gradients exist
        for param in model.parameters():
            assert param.grad is not None


class TestAsymmetricLoss:
    """Test the asymmetric safety-first loss function."""

    def test_loss_initialization(self):
        """Loss should initialize with correct penalty."""
        loss_fn = AsymmetricLoss(late_penalty=5.0)
        assert loss_fn.late_penalty == 5.0

    def test_loss_perfect_prediction(self):
        """Loss should be zero for perfect predictions."""
        loss_fn = AsymmetricLoss(late_penalty=5.0)
        pred = torch.tensor([10.0, 20.0, 30.0])
        target = torch.tensor([10.0, 20.0, 30.0])
        loss = loss_fn(pred, target)
        assert loss.item() == pytest.approx(0.0, abs=1e-6)

    def test_loss_underestimation_penalized_more(self):
        """Underestimation (pred < target) should have higher loss."""
        loss_fn = AsymmetricLoss(late_penalty=5.0)

        # Underestimation: pred=10, target=20
        pred_under = torch.tensor([10.0])
        target = torch.tensor([20.0])
        loss_under = loss_fn(pred_under, target)

        # Overestimation: pred=30, target=20
        pred_over = torch.tensor([30.0])
        loss_over = loss_fn(pred_over, target)

        # Underestimation should be penalized more
        assert loss_under > loss_over

    def test_loss_overestimation_lower_penalty(self):
        """Overestimation should have lower penalty than underestimation."""
        loss_fn = AsymmetricLoss(late_penalty=5.0)

        # Same absolute error, but overestimation
        pred_over = torch.tensor([30.0])
        target = torch.tensor([20.0])
        loss_over = loss_fn(pred_over, target)

        # Underestimation with same absolute error
        pred_under = torch.tensor([10.0])
        loss_under = loss_fn(pred_under, target)

        # The ratio should be approximately the penalty factor
        ratio = loss_under / loss_over
        assert ratio > 1.0  # Underestimation is penalized more


class TestTrainingFunction:
    """Test the training function."""

    def test_train_function_signature(self):
        """Train function should accept expected parameters."""
        import inspect
        from src.train_model import train

        sig = inspect.signature(train)
        params = list(sig.parameters.keys())
        assert "epochs" in params
        assert "batch_size" in params
        assert "learning_rate" in params
        assert "hidden_size" in params
        assert "num_layers" in params
        assert "patience" in params
        assert "val_split" in params
        assert "data_path" in params

    def test_train_function_defaults(self):
        """Train function should have sensible defaults."""
        import inspect
        from src.train_model import train

        sig = inspect.signature(train)
        assert sig.parameters["epochs"].default == 50
        assert sig.parameters["batch_size"].default == 32
        assert sig.parameters["learning_rate"].default == 0.001
        assert sig.parameters["patience"].default == 10
        assert sig.parameters["val_split"].default == 0.2
