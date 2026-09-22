import { notFound } from "next/navigation";
import { getSpace, listDocuments } from "@/lib/api";
import { Workspace } from "@/components/Workspace";

export default async function SpacePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [space, documents] = await Promise.all([getSpace(id), listDocuments(id)]);
  if (!space) notFound();

  return <Workspace space={space} documents={documents} />;
}
