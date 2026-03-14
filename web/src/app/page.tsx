import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
const GITHUB_REPO_URL = "https://github.com/bowen511code/ket-grammar-coach";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col justify-center p-8 max-w-xl mx-auto">
      <h1 className="text-2xl font-bold">KET Grammar Coach Demo</h1>
      <p className="mt-2 text-gray-600">
        学生端：做题、请求提示、提交答案并查看反馈；教师端：查看作答记录。
      </p>
      <nav aria-label="Demo navigation" className="mt-4 flex gap-4">
        <Link href="/demo/student" className="text-blue-600 underline">
          Student Demo
        </Link>
        <Link href="/demo/teacher" className="text-blue-600 underline">
          Teacher Demo
        </Link>
      </nav>
      <p className="mt-6 text-sm text-gray-500">
        <a
          href={`${API_BASE}/health`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 underline"
        >
          API health
        </a>
        {" · "}
        <a
          href={GITHUB_REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 underline"
        >
          GitHub repo
        </a>
      </p>
    </main>
  );
}
