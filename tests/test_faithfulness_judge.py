from evals.faithfulness.judge import HeuristicJudge
from evals.faithfulness.run import calibrate, load_cases


async def test_grounded_answer_is_faithful() -> None:
	verdict = await HeuristicJudge().judge(
		answer="You needed to call Tunde about the pricing thing before Thursday [1].",
		cited=["need to call Tunde about the pricing thing before Thursday"],
	)
	assert verdict.faithful and verdict.unsupported == []


async def test_invented_claim_is_flagged_verbatim() -> None:
	verdict = await HeuristicJudge().judge(
		answer=(
			"You needed to call Tunde about pricing [1]. "
			"He also asked you to review the contract draft and send the slides."
		),
		cited=["need to call Tunde about the pricing thing before Thursday"],
	)
	assert not verdict.faithful
	assert verdict.unsupported == [
		"He also asked you to review the contract draft and send the slides."
	]


async def test_saying_the_notes_are_silent_is_not_a_claim() -> None:
	verdict = await HeuristicJudge().judge(
		answer="Your notes don't mention anything about extraction.", cited=[]
	)
	assert verdict.faithful


async def test_heuristic_judge_is_calibrated_on_the_case_file() -> None:
	accuracy, wrong = await calibrate(HeuristicJudge(), load_cases())
	assert accuracy >= 0.9, wrong
