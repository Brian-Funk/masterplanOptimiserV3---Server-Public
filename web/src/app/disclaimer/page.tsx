import Link from "next/link";

export default function DisclaimerPage() {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100 mb-8">
          Disclaimer
        </h1>

        <div className="space-y-6 text-gray-700 dark:text-gray-300">
          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              General
            </h2>
            <p>
              Masterplan Optimiser is a scheduling and resource-allocation tool
              designed to assist event organisers. All optimised schedules,
              assignments, and suggestions produced by this software are{" "}
              <strong>advisory only</strong> and should be reviewed by a
              qualified person before being acted upon.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              No Warranty
            </h2>
            <p>
              This software is provided <strong>&quot;as is&quot;</strong>,
              without warranty of any kind, express or implied, including but
              not limited to the warranties of merchantability, fitness for a
              particular purpose, and non-infringement. The author shall not be
              liable for any claim, damages, or other liability arising from the
              use of the software.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Limitation of Liability
            </h2>
            <p>
              In no event shall the author be held liable for any indirect,
              incidental, special, consequential, or punitive damages, including
              but not limited to loss of data, scheduling errors, or missed
              deadlines, arising out of or in connection with the use of this
              software.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Third-Party Services
            </h2>
            <p>
              This application may integrate with third-party services such as
              Google Calendar. The author is not responsible for the
              availability, accuracy, or security of those services. Use of
              third-party integrations is subject to their respective terms and
              conditions.
            </p>
          </section>
        </div>

        <div className="mt-12 pt-6 border-t border-gray-200 dark:border-gray-700">
          <Link
            href="/login"
            className="text-blue-600 dark:text-blue-400 hover:underline text-sm"
          >
            &larr; Back to Login
          </Link>
        </div>
      </div>
    </div>
  );
}
