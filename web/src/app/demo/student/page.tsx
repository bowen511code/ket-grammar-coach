"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

type GrammarPoint = {
  id: string;
  label: string;
  description: string;
};

type Question = {
  id: string;
  type: string;
  text: string;
  options?: string[];
  grammar_point?: string;
  grammar_point_id?: string;
};

type SubmitResult = {
  question_id: string;
  correct: boolean;
  feedback: string;
  expected_answer: string;
  explanation?: string;
  grammar_point_id?: string;
};

export default function StudentDemoPage() {
  const [grammarPoints, setGrammarPoints] = useState<GrammarPoint[]>([]);
  const [selectedGrammarPointId, setSelectedGrammarPointId] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string>("");
  const [hint, setHint] = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<SubmitResult | null>(null);
  const [loadingGrammarPoints, setLoadingGrammarPoints] = useState(true);
  const [loadingQuestion, setLoadingQuestion] = useState(false);
  const [loadingHint, setLoadingHint] = useState(false);
  const [loadingSubmit, setLoadingSubmit] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchQuestion = useCallback(async () => {
    setLoadingQuestion(true);
    setError(null);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);
    const params = new URLSearchParams();
    if (selectedGrammarPointId != null) params.set("grammar_point", selectedGrammarPointId);
    params.set("_ts", String(Date.now()));
    const url = `${API_BASE}/api/question?${params.toString()}`;
    try {
      const res = await fetch(url, {
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error("获取题目失败");
      const data = await res.json();
      setQuestion(data);
      setHint(null);
      setSubmitResult(null);
      setSelectedAnswer("");
    } catch (e) {
      clearTimeout(timeoutId);
      if (e instanceof Error) {
        if (e.name === "AbortError") {
          setError("请求超时，请确认后端已启动（端口 8001）");
        } else if (e.message === "Failed to fetch" || e.message.includes("fetch")) {
          setError("无法连接后端，请确认后端已启动（端口 8001）并刷新重试");
        } else {
          setError(e.message || "获取题目失败");
        }
      } else {
        setError("获取题目失败");
      }
    } finally {
      setLoadingQuestion(false);
    }
  }, [selectedGrammarPointId]);

  useEffect(() => {
    let cancelled = false;
    setLoadingGrammarPoints(true);
    setError(null);
    fetch(`${API_BASE}/api/grammar_points`)
      .then((res) => {
        if (!res.ok) throw new Error("grammar_points_failed");
        return res.json();
      })
      .then((data: GrammarPoint[]) => {
        if (cancelled) return;
        setLoadingGrammarPoints(false);
        if (Array.isArray(data) && data.length > 0) {
          setGrammarPoints(data);
          setSelectedGrammarPointId(data[0].id);
        } else {
          setGrammarPoints([]);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadingGrammarPoints(false);
          setGrammarPoints([]);
          setError("无法加载语法点，请确认后端已启动（端口 8001）");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedGrammarPointId != null) {
      fetchQuestion();
    }
  }, [selectedGrammarPointId, fetchQuestion]);

  const handleGetHint = async () => {
    if (!question) return;
    setLoadingHint(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/hint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: question.id }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "请求提示失败");
      }
      const data = await res.json();
      setHint(data.hint);
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求提示失败");
    } finally {
      setLoadingHint(false);
    }
  };

  const handleSubmit = async () => {
    if (!question) return;
    if (!selectedAnswer) return;
    setLoadingSubmit(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_id: question.id,
          answer: selectedAnswer,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "提交失败");
      }
      const data = await res.json();
      setSubmitResult({
        question_id: data.question_id,
        correct: data.correct,
        feedback: data.feedback,
        expected_answer: data.expected_answer,
        explanation: data.explanation,
        grammar_point_id: data.grammar_point_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
    } finally {
      setLoadingSubmit(false);
    }
  };

  return (
    <main className="min-h-screen flex flex-col justify-center p-8 max-w-xl mx-auto">
      <h1 className="text-2xl font-bold">学生端 Demo</h1>

      {error && (
        <p className="mt-2 p-2 bg-red-100 text-red-800 rounded text-sm">
          {error}
        </p>
      )}

      {!loadingGrammarPoints && grammarPoints.length > 0 && (
        <div className="mt-4">
          <label htmlFor="grammar-point" className="block text-sm font-medium text-gray-700 mb-1">
            语法点
          </label>
          <select
            id="grammar-point"
            value={selectedGrammarPointId ?? ""}
            onChange={(e) => setSelectedGrammarPointId(e.target.value || null)}
            className="w-full max-w-xs px-3 py-2 border border-gray-300 rounded text-sm bg-white"
          >
            {grammarPoints.map((gp) => (
              <option key={gp.id} value={gp.id}>
                {gp.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {loadingGrammarPoints ? (
        <p className="mt-4 text-gray-500">加载语法点中…</p>
      ) : grammarPoints.length === 0 ? (
        <p className="mt-4 text-gray-500">无法加载语法点，请确认后端已启动（端口 8001）后刷新页面。</p>
      ) : loadingQuestion ? (
        <p className="mt-4 text-gray-500">加载题目中…</p>
      ) : question ? (
        <>
          <p className="mt-4 font-medium text-gray-700">{question.text}</p>
          {question.grammar_point && (
            <p className="mt-1 text-sm text-gray-500">
              语法点：{question.grammar_point}
            </p>
          )}

          {question.options && question.options.length > 0 && (
            <div className="mt-4">
              <p className="text-sm text-gray-600 mb-2">选项：</p>
              <ul className="flex flex-wrap gap-2">
                {question.options.map((opt, idx) => (
                  <li key={`opt-${idx}`}>
                    <button
                      type="button"
                      onClick={() => setSelectedAnswer(opt)}
                      className={`px-3 py-1.5 rounded border text-sm ${
                        selectedAnswer === opt
                          ? "border-blue-600 bg-blue-50 text-blue-800"
                          : "border-gray-300 bg-white hover:bg-gray-50"
                      }`}
                    >
                      {opt}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-6 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleGetHint}
              disabled={loadingHint}
              className="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50 text-sm"
            >
              {loadingHint ? "请求中…" : "获取提示"}
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={loadingSubmit || !selectedAnswer}
              className="px-4 py-2 bg-blue-600 text-white hover:bg-blue-700 rounded disabled:opacity-50 text-sm"
            >
              {loadingSubmit ? "提交中…" : "提交答案"}
            </button>
            <button
              type="button"
              onClick={fetchQuestion}
              disabled={loadingQuestion}
              className="px-4 py-2 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 text-sm"
            >
              重新加载题目
            </button>
          </div>

          {hint !== null && (
            <div className="mt-4 p-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-900">
              <span className="font-medium">提示：</span> {hint}
            </div>
          )}

          {submitResult && (
            <div className="mt-4 p-3 border rounded text-sm space-y-1">
              <p>
                <span className="font-medium">结果：</span>
                {submitResult.correct ? "答对了" : "答错了"}
              </p>
              <p>{submitResult.feedback}</p>
              <p className="text-gray-600">
                正确答案：{submitResult.expected_answer}
              </p>
              {submitResult.explanation && submitResult.explanation.trim() !== "" && (
                <p className="text-gray-700 mt-2">
                  <span className="font-medium">讲解：</span> {submitResult.explanation}
                </p>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="mt-4 text-gray-500">加载题目失败或暂无题目</p>
      )}
    </main>
  );
}
