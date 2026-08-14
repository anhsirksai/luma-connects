import type { PersonCard as PersonCardType } from "../../lib/api-shape";
import { PersonCard } from "./PersonCard";

export function PersonCardList({ cards }: { cards: PersonCardType[] }) {
  if (cards.length === 0) return null;

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {cards.map((card) => (
        <PersonCard key={card.person_id} person={card} />
      ))}
    </div>
  );
}
