"""成本估算定价测试：缓存命中/未命中分段计价（第 4 课 §5 的真实验证）。"""
from litecode.core.agent_loop import AgentLoop
from litecode.core.kernel import Kernel


def _loop(pricing):
    return AgentLoop(Kernel("cost-test"), adapter=None, registry=None, pricing=pricing)


def test_cost_charges_cache_hit_at_discount():
    loop = _loop({"input_per_mtok": 1.6, "output_per_mtok": 4.8, "cache_hit_per_mtok": 0.16})
    # 900K 命中 + 100K 未命中 + 50K 输出
    stats = {"cache_hit_tokens": 900_000, "cache_miss_tokens": 100_000, "output_tokens": 50_000}
    cost = loop._estimate_cost(stats)
    expected = 0.1 * 1.6 + 0.9 * 0.16 + 0.05 * 4.8  # 0.16 + 0.144 + 0.24 = 0.544
    assert abs(cost - expected) < 1e-9
    # 对照：旧算法（全按 input 全价）会算 1.5*1.6+0.24 = 2.64，高估近 5 倍
    assert cost < 2.64


def test_cost_defaults_cache_price_to_tenth_of_input():
    # 未配置 cache_hit_per_mtok：默认 input 的 10%
    loop = _loop({"input_per_mtok": 2.0, "output_per_mtok": 6.0})
    stats = {"cache_hit_tokens": 1_000_000, "cache_miss_tokens": 0, "output_tokens": 0}
    assert abs(loop._estimate_cost(stats) - 0.2) < 1e-9


def test_cost_falls_back_to_input_tokens_without_usage():
    # 估算兜底（无 usage）：hit/miss 均为 0，按 input_tokens 全价
    loop = _loop({"input_per_mtok": 1.0, "output_per_mtok": 2.0, "cache_hit_per_mtok": 0.1})
    stats = {"input_tokens": 500_000, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "output_tokens": 100_000}
    assert abs(loop._estimate_cost(stats) - (0.5 * 1.0 + 0.1 * 2.0)) < 1e-9


def test_zero_pricing_is_zero():
    # 显式零价（pricing=None 会启用内置默认价，故此处显式传零）
    loop = _loop({"input_per_mtok": 0, "output_per_mtok": 0, "cache_hit_per_mtok": 0})
    stats = {"cache_hit_tokens": 100, "cache_miss_tokens": 100, "output_tokens": 100}
    assert loop._estimate_cost(stats) == 0.0
