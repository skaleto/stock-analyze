import unittest

import torch

from stock_analyze.research.deep.temporal_model import TemporalContextNet


class TemporalModelTest(unittest.TestCase):
    def test_multi_horizon_forward_and_backward(self):
        model = TemporalContextNet(
            sequence_feature_dim=6,
            static_dim=5,
            horizons=(3, 5, 10, 20),
            hidden_dim=24,
            context_dim=12,
            gru_layers=2,
            dropout=0.0,
        )
        batch_size = 16
        sequence = torch.randn(batch_size, 12, 6)
        validity = torch.ones(batch_size, 12, 6)
        lengths = torch.tensor([12, 11, 10, 9] * 4)
        static = torch.randn(batch_size, 5)
        industry = torch.randn(batch_size, 6)
        market = torch.randn(batch_size, 6)

        outputs = model(sequence, validity, lengths, static, industry, market)
        loss = sum(
            logits.square().mean() + returns.square().mean()
            for logits, returns in outputs.values()
        )
        loss.backward()

        self.assertEqual(set(outputs), {3, 5, 10, 20})
        for logits, returns in outputs.values():
            self.assertEqual(tuple(logits.shape), (batch_size, 3))
            self.assertEqual(tuple(returns.shape), (batch_size,))
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))
        self.assertEqual(model.architecture()["family"], "temporal_context_gru")


if __name__ == "__main__":
    unittest.main()
