"""The beta distribution, checked against closed forms rather than another library.

Testing a numerical routine against SciPy would only establish that two
implementations agree, which is worth less than it looks: they would agree on
a shared misunderstanding too. The regularised incomplete beta has exact
closed forms at integer and half-integer shape parameters, and those are what
these tests use, so a failure means the arithmetic is wrong rather than
different.
"""

from __future__ import annotations

import math

import pytest

from cass_converter.beta import (
    BetaError,
    cdf,
    partial_expectation,
    regularised_incomplete_beta,
    shape_parameters,
)

#: Accuracy the continued fraction is asked to hold to. It reaches about 2e-15
#: against the closed forms; this leaves room for platform libm differences
#: without leaving room for a wrong algorithm.
TOLERANCE = 1e-12


def binomial_form(a: int, b: int, x: float) -> float:
    """I_x(a, b) for integer shapes, as the upper tail of a binomial.

    The identity is exact: a beta with integer shapes is the distribution of an
    order statistic, so its CDF is a finite sum of binomial terms and needs no
    numerical method at all.
    """
    n = a + b - 1
    return sum(math.comb(n, j) * x**j * (1 - x) ** (n - j) for j in range(a, n + 1))


# -- the CDF ---------------------------------------------------------------------

@pytest.mark.parametrize("a", range(1, 7))
@pytest.mark.parametrize("b", range(1, 7))
@pytest.mark.parametrize("x", [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99])
def test_integer_shapes_match_the_binomial_closed_form(a, b, x):
    assert regularised_incomplete_beta(a, b, x) == pytest.approx(
        binomial_form(a, b, x), abs=TOLERANCE
    )


@pytest.mark.parametrize("x", [0.05, 0.3, 0.5, 0.8, 0.95])
def test_half_integer_shapes_match_the_arcsine_law(x):
    """I_x(1/2, 1/2) is the arcsine distribution, which has a closed form."""
    expected = (2 / math.pi) * math.asin(math.sqrt(x))
    assert regularised_incomplete_beta(0.5, 0.5, x) == pytest.approx(
        expected, abs=TOLERANCE
    )


@pytest.mark.parametrize("x", [0.1, 0.4, 0.7, 0.95])
def test_a_uniform_beta_is_the_identity(x):
    assert regularised_incomplete_beta(1, 1, x) == pytest.approx(x, abs=TOLERANCE)


@pytest.mark.parametrize(("a", "b", "x"), [(2.5, 3.5, 0.4), (0.3, 7.1, 0.15)])
def test_the_symmetry_relation_holds(a, b, x):
    """I_x(a, b) = 1 - I_{1-x}(b, a), which the implementation relies on."""
    assert regularised_incomplete_beta(a, b, x) == pytest.approx(
        1 - regularised_incomplete_beta(b, a, 1 - x), abs=TOLERANCE
    )


def test_the_cdf_is_monotone_across_its_range():
    values = [regularised_incomplete_beta(2.0, 5.0, x / 100) for x in range(101)]
    assert values == sorted(values)
    assert values[0] == 0.0
    assert values[-1] == 1.0


@pytest.mark.parametrize(("a", "b"), [(0, 1), (1, 0), (-1, 2)])
def test_a_non_positive_shape_is_refused(a, b):
    with pytest.raises(BetaError, match="must be positive"):
        regularised_incomplete_beta(a, b, 0.5)


# -- mean and CoV to shape parameters ----------------------------------------------

@pytest.mark.parametrize(
    ("mean", "cov"),
    [(0.5, 0.2), (0.01, 3.0), (0.9, 0.1), (0.000144187, 19.5585)],
)
def test_the_shape_parameters_reproduce_the_mean_and_cov(mean, cov):
    """The relation has to invert, or every discretised function is misparameterised."""
    a, b = shape_parameters(mean, cov)
    assert a / (a + b) == pytest.approx(mean, rel=1e-12)

    variance = a * b / ((a + b) ** 2 * (a + b + 1))
    assert math.sqrt(variance) / mean == pytest.approx(cov, rel=1e-12)


def test_a_cov_implying_impossible_spread_is_refused_not_clamped():
    """A variance at or above m(1-m) is not a beta distribution.

    Clamping would substitute a different distribution for the published one
    and attribute the loss it produced to GEM.
    """
    with pytest.raises(BetaError, match="not a beta distribution"):
        shape_parameters(0.5, 1.5)


@pytest.mark.parametrize("mean", [0.0, 1.0, -0.1, 1.2])
def test_a_mean_outside_the_open_unit_interval_is_refused(mean):
    with pytest.raises(BetaError, match="strictly between 0 and 1"):
        shape_parameters(mean, 0.5)


def test_a_non_positive_cov_is_refused():
    with pytest.raises(BetaError, match="positive CoV"):
        shape_parameters(0.5, 0.0)


# -- the partial expectation ---------------------------------------------------------

@pytest.mark.parametrize(("mean", "cov"), [(0.3, 0.5), (0.05, 2.0), (0.8, 0.3)])
def test_the_partial_expectation_over_the_whole_range_is_the_mean(mean, cov):
    assert partial_expectation(mean, cov, 0.0, 1.0) == pytest.approx(mean, rel=1e-11)


@pytest.mark.parametrize(("mean", "cov"), [(0.3, 0.5), (0.05, 2.0)])
def test_partial_expectations_over_a_partition_sum_to_the_mean(mean, cov):
    """What makes the mean-preserving placement exact rather than approximate."""
    edges = [index / 20 for index in range(21)]
    total = sum(
        partial_expectation(mean, cov, low, high)
        for low, high in zip(edges, edges[1:], strict=False)
    )
    assert total == pytest.approx(mean, rel=1e-11)


def test_the_partial_expectation_matches_a_numerical_integral():
    """Independent confirmation, by a method that shares no code with it."""
    mean, cov = 0.25, 0.8
    a, b = shape_parameters(mean, cov)
    steps = 200_000
    low, high = 0.1, 0.4
    width = (high - low) / steps
    density = lambda x: (  # noqa: E731 - a one-line integrand reads better inline
        x ** (a - 1)
        * (1 - x) ** (b - 1)
        / math.exp(math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    )
    integral = sum(
        (low + (index + 0.5) * width) * density(low + (index + 0.5) * width) * width
        for index in range(steps)
    )
    assert partial_expectation(mean, cov, low, high) == pytest.approx(
        integral, rel=1e-06
    )


def test_an_empty_interval_contributes_nothing():
    assert partial_expectation(0.3, 0.5, 0.4, 0.4) == 0.0


# -- the convenience wrapper -----------------------------------------------------

def test_the_cdf_wrapper_agrees_with_the_shape_parameterisation():
    mean, cov = 0.4, 0.6
    a, b = shape_parameters(mean, cov)
    assert cdf(mean, cov, 0.35) == regularised_incomplete_beta(a, b, 0.35)
