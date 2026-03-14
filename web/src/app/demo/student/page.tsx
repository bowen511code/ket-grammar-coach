export default function StudentDemoPage() {
  return (
    <main className="min-h-screen flex flex-col justify-center p-8 max-w-xl mx-auto">
      <h1 className="text-2xl font-bold">学生端 Demo</h1>
      <p className="mt-2 text-gray-600">
        此页面未来将展示学生做题流程，包括获取题目、请求提示、提交答案与查看反馈（对应
        /api/question、/api/hint、/api/submit）。
      </p>
    </main>
  );
}
