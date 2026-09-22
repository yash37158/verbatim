import Link from "next/link";
import { FileText, FolderPlus } from "lucide-react";
import { listSpaces } from "@/lib/api";
import { Empty, formatWhen } from "@/components/ui";
import { NewSpaceDialog } from "@/components/NewSpaceDialog";

export default async function SpacesPage() {
  const spaces = await listSpaces();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10">
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl tracking-tight">Spaces</h1>
            <p className="mt-1 text-sm text-muted">
              A Space is a set of documents you ask questions across.
            </p>
          </div>
          <NewSpaceDialog />
        </div>

        {spaces.length === 0 ? (
          <Empty icon={<FolderPlus className="size-5" />} title="No Spaces yet">
            <p className="mb-4">Create a Space, drop in a few documents, and start asking.</p>
            <NewSpaceDialog />
          </Empty>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {spaces.map((space) => (
              <li key={space.id}>
                <Link
                  href={`/spaces/${space.id}`}
                  className="group flex h-full flex-col rounded-xl border border-line bg-surface p-4 transition-colors hover:border-faint"
                >
                  <h2 className="font-display text-base tracking-tight">{space.name}</h2>
                  <p className="mt-1.5 line-clamp-2 flex-1 text-sm leading-relaxed text-muted">
                    {space.description}
                  </p>
                  <div className="mt-4 flex items-center gap-3 border-t border-line pt-3 text-xs text-faint">
                    <span className="inline-flex items-center gap-1.5">
                      <FileText className="size-3.5" />
                      {space.document_count} {space.document_count === 1 ? "document" : "documents"}
                    </span>
                    <span aria-hidden>·</span>
                    <span>{formatWhen(space.last_activity_at)}</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
