import unittest

import numpy as np

from stock_analyze.research.deep.model import (
    DualHeadTabularNet,
    combined_objective,
    deterministic_pairwise_rank_loss,
)


class DeepModelTest(unittest.TestCase):
    def test_dual_head_shapes_and_backward(self):
        import torch

        model = DualHeadTabularNet(input_dim=12, hidden_dim=32, bottleneck_dim=16, dropout=0.0)
        features = torch.randn(24, 12)
        labels = torch.tensor([0, 1, 2] * 8)
        returns = torch.linspace(-0.03, 0.03, 24)
        date_groups = torch.tensor(np.repeat(np.arange(6), 4))

        logits, predicted_returns = model(features)
        loss, components = combined_objective(
            logits,
            predicted_returns,
            labels,
            returns,
            date_groups,
            return_scale=100.0,
        )
        loss.backward()

        self.assertEqual(tuple(logits.shape), (24, 3))
        self.assertEqual(tuple(predicted_returns.shape), (24,))
        self.assertTrue(torch.isfinite(loss))
        self.assertEqual(set(components), {"classification", "regression", "ranking"})
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

    def test_pairwise_loss_rewards_correct_cross_sectional_order(self):
        import torch

        target = torch.tensor([-0.03, -0.01, 0.01, 0.04, -0.02, 0.00, 0.02, 0.05])
        date_groups = torch.tensor([0, 0, 0, 0, 1, 1, 1, 1])
        correct = target * 100.0
        reversed_order = -correct

        correct_loss = deterministic_pairwise_rank_loss(correct, target, date_groups)
        reversed_loss = deterministic_pairwise_rank_loss(reversed_order, target, date_groups)

        self.assertLess(float(correct_loss), float(reversed_loss))


if __name__ == "__main__":
    unittest.main()
