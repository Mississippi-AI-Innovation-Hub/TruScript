import { useState, type FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { ArrowLeft, PlusCircle } from 'lucide-react';
import toast from 'react-hot-toast';
import { transcriptsApi } from '../api';
import { PageHeader } from '../components/PageHeader';
import { Spinner } from '../components/Spinner';
import type { ApplicantType } from '../types';

export function CreateTranscript() {
  const navigate = useNavigate();
  const [applicantId, setApplicantId] = useState('');
  const [applicantType, setApplicantType] = useState<ApplicantType>('first_time');
  const [assignedStaffId, setAssignedStaffId] = useState('');

  const mutation = useMutation({
    mutationFn: () =>
      transcriptsApi.create({
        applicant_id: applicantId.trim(),
        applicant_type: applicantType,
        assigned_staff_id: assignedStaffId.trim() || null,
      }),
    onSuccess: (data) => {
      toast.success('Verification record created.');
      navigate(`/transcripts/${data.verification_id}`);
    },
    onError: () => toast.error('Failed to create verification record.'),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <div className="p-8 max-w-2xl">
      <Link
        to="/transcripts"
        className="inline-flex items-center gap-1 text-sm text-brand-500 hover:text-brand-700 mb-4"
      >
        <ArrowLeft size={14} /> Back to Verifications
      </Link>

      <PageHeader
        title="New Transcript Verification"
        subtitle="Submit a new applicant transcript record for AI-assisted verification."
      />

      <div className="card">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Applicant ID */}
          <div>
            <label htmlFor="applicant-id" className="label">
              Applicant ID <span className="text-red-400">*</span>
            </label>
            <input
              id="applicant-id"
              type="text"
              className="input"
              placeholder="e.g. APP-2025-00123"
              value={applicantId}
              onChange={(e) => setApplicantId(e.target.value)}
              required
            />
            <p className="text-xs text-gray-400 mt-1">
              Unique identifier assigned to the nursing applicant.
            </p>
          </div>

          {/* Applicant Type */}
          <div>
            <p className="label">
              Applicant Type <span className="text-red-400">*</span>
            </p>
            <div className="flex gap-4">
              {([
                { value: 'first_time', label: 'First-Time Licensure', desc: 'Applying for initial nursing license in Mississippi.' },
                { value: 'endorsement', label: 'Endorsement', desc: 'Licensed in another state, seeking Mississippi endorsement.' },
              ] as const).map(({ value, label, desc }) => (
                <label
                  key={value}
                  className={`flex-1 flex items-start gap-3 p-4 rounded-xl border-2 cursor-pointer transition-colors ${
                    applicantType === value
                      ? 'border-brand-400 bg-brand-50'
                      : 'border-gray-200 hover:border-brand-200'
                  }`}
                >
                  <input
                    type="radio"
                    name="applicant-type"
                    value={value}
                    checked={applicantType === value}
                    onChange={() => setApplicantType(value)}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-sm font-semibold text-gray-800">{label}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Assigned Staff */}
          <div>
            <label htmlFor="staff-id" className="label">
              Assign to Staff <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              id="staff-id"
              type="text"
              className="input"
              placeholder="Staff user ID"
              value={assignedStaffId}
              onChange={(e) => setAssignedStaffId(e.target.value)}
            />
            <p className="text-xs text-gray-400 mt-1">
              Leave blank to assign later.
            </p>
          </div>

          {/* Info box */}
          <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 text-sm text-blue-800">
            <p className="font-semibold mb-1">What happens next?</p>
            <ul className="list-disc list-inside space-y-1 text-xs text-blue-700">
              <li>The record is created with <strong>pending</strong> status.</li>
              <li>The AI system will extract and analyze the transcript when documents are attached.</li>
              <li>Flags and a verification summary will be generated for staff review.</li>
              <li>Staff can confirm or override AI flags and add annotations.</li>
            </ul>
          </div>

          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={!applicantId.trim() || mutation.isPending}
              className="btn-primary flex items-center gap-2 text-sm py-2.5 px-5"
            >
              {mutation.isPending ? <Spinner size={16} /> : <PlusCircle size={16} />}
              {mutation.isPending ? 'Creating…' : 'Create Verification Record'}
            </button>
            <Link to="/transcripts" className="btn-secondary text-sm py-2.5 px-5">
              Cancel
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}
