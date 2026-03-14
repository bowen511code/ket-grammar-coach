"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

function formatTime(created_at: string): string {
  const d = new Date(created_at);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${day} ${h}:${min}`;
}

type Attempt = {
  id: string;
  question_id: string;
  answer: string;
  correct: boolean;
  created_at: string;
  grammar_point_id?: string;
  question_text?: string;
  explanation?: string;
};

export default function TeacherDemoPage() {
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAttempts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/attempts`);
      if (!res.ok) throw new Error("获取作答记录失败");
      const data = await res.json();
      setAttempts(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "获取作答记录失败");
      setAttempts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAttempts();
  }, [fetchAttempts]);

  return (
    <main className="min-h-screen flex flex-col justify-center p-8 max-w-xl mx-auto">
      <h1 className="text-2xl font-bold">教师端 Demo</h1>
      <p className="mt-2 text-gray-600">
        查看学生作答记录（来自 GET /api/attempts），按时间倒序。
      </p>

      {error && (
        <p className="mt-4 p-2 bg-red-100 text-red-800 rounded text-sm">
          {error}
        </p>
      )}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={fetchAttempts}
          disabled={loading}
          className="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50 text-sm"
        >
          {loading ? "加载中…" : "刷新记录"}
        </button>
      </div>

      {loading ? (
        <p className="mt-4 text-gray-500">加载作答记录中…</p>
      ) : attempts.length === 0 ? (
        <p className="mt-4 text-gray-500">
          暂无作答记录，请先在学生端提交答案。
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full border border-gray-200 text-sm">
            <thead>
              <tr className="bg-gray-50">
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  id
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  question_id
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  answer
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  correct
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  created_at
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  grammar_point_id
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  question_text
                </th>
                <th className="border-b border-gray-200 px-3 py-2 text-left font-medium">
                  explanation
                </th>
              </tr>
            </thead>
            <tbody>
              {attempts.map((a) => (
                <tr key={a.id} className="border-b border-gray-100">
                  <td className="px-3 py-2">{a.id}</td>
                  <td className="px-3 py-2">{a.question_id}</td>
                  <td className="px-3 py-2">{a.answer}</td>
                  <td className="px-3 py-2">
                    {a.correct ? (
                      <span className="text-green-600">正确</span>
                    ) : (
                      <span className="text-red-600">错误</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-600">
                    {formatTime(a.created_at)}
                  </td>
                  <td className="px-3 py-2 text-gray-600">
                    {a.grammar_point_id != null && a.grammar_point_id !== ""
                      ? a.grammar_point_id
                      : "-"}
                  </td>
                  <td className="px-3 py-2 text-gray-700 max-w-[200px] break-words">
                    {a.question_text != null && a.question_text !== ""
                      ? a.question_text
                      : "-"}
                  </td>
                  <td className="px-3 py-2 text-gray-600 max-w-[220px] break-words">
                    {a.explanation != null && a.explanation !== ""
                      ? a.explanation
                      : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
