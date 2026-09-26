import { notFound } from "next/navigation";
import { getSpace, listConversations, listDocuments, listMessages } from "@/lib/api";
import { Workspace } from "@/components/Workspace";

export default async function SpacePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ c?: string }>;
}) {
  const [{ id }, { c }] = await Promise.all([params, searchParams]);
  // All four in one round, including the deep-linked conversation's messages: `?c=` is
  // enough to fetch them, so there is no reason to wait for the conversations list first.
  // Preloading here rather than after mount means a reload restores the thread with no
  // flash of empty state.
  const [space, documents, conversations, messages] = await Promise.all([
    getSpace(id),
    listDocuments(id),
    listConversations(id),
    c ? listMessages(c).catch(() => []) : Promise.resolve([]),
  ]);
  if (!space) notFound();

  // Only honour `?c=` if it really belongs to this Space.
  const active = conversations.find((x) => x.id === c) ?? null;
  const initialMessages = active ? messages : [];

  return (
    <Workspace
      space={space}
      documents={documents}
      conversations={conversations}
      activeConversation={active}
      initialMessages={initialMessages}
    />
  );
}
